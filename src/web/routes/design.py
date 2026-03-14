from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from src.session import load_session
from src.agent import (
    DesignAgent, DESIGN_TOOLS,
    MODEL, THINKING_BUDGET, TOKEN_BUDGET,
    build_design_prompt, prune_messages,
)
from src.pipeline.design import parse_design, validate_design
from src.pipeline.config import get_printer
from src.web.naming import generate_session_name
from fastapi.responses import StreamingResponse

import anthropic

from src.web.routes._deps import (
    get_catalog, load_session_or_404, invalidate_downstream,
    enrich_components, enrich_design_3d,
)

router = APIRouter(tags=["design"])


@router.post("/sessions/{sid}/design")
async def run_design(sid: str, request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "Missing 'prompt' in request body")

    sess = load_session_or_404(sid)
    cat = get_catalog()

    async def event_stream():
        try:
            agent = DesignAgent(cat, sess)
            async for event in agent.run(prompt):
                if event.type == "design" and event.data:
                    design = event.data.get("design")
                    if design:
                        enrich_components(design.get("ui_placements", []), cat)
                        enrich_design_3d(design)
                data = json.dumps(event.data) if event.data else "{}"
                yield f"event: {event.type}\ndata: {data}\n\n"

                if event.type == "design":
                    name = generate_session_name(sess)
                    if name:
                        yield f"event: session_named\ndata: {json.dumps({'name': name})}\n\n"
        except Exception as e:
            data = json.dumps({"message": str(e)})
            yield f"event: error\ndata: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{sid}/design")
async def get_design(sid: str):
    s = load_session_or_404(sid)
    data = s.read_artifact("design.json")
    if data is None:
        raise HTTPException(404, "No design yet")
    cat = get_catalog()
    enrich_components(data.get("ui_placements", []), cat)
    enrich_design_3d(data)
    return data


@router.put("/sessions/{sid}/design")
async def put_design(sid: str, request: Request):
    body = await request.json()
    s = load_session_or_404(sid)
    cat = get_catalog()

    validation_body = {**body}
    if not validation_body.get("components"):
        ui_components = []
        for p in validation_body.get("ui_placements", []):
            comp: dict = {
                "catalog_id": p.get("catalog_id", p["instance_id"]),
                "instance_id": p["instance_id"],
            }
            if p.get("mounting_style"):
                comp["mounting_style"] = p["mounting_style"]
            ui_components.append(comp)
        validation_body["components"] = ui_components
    validation_body.setdefault("nets", [])

    try:
        spec = parse_design(validation_body)
    except (KeyError, TypeError, ValueError, IndexError) as e:
        raise HTTPException(400, f"Design parsing error: {e}")

    errors = validate_design(spec, cat, printer=get_printer(s.printer_id))
    if errors:
        raise HTTPException(422, detail={"errors": errors})

    s.write_artifact("design.json", body)
    s.pipeline_state["design"] = "complete"
    invalidate_downstream(s, "design")
    s.save()

    enrich_components(body.get("ui_placements", []), cat)
    enrich_design_3d(body)
    return body


@router.get("/sessions/{sid}/design/conversation")
async def get_design_conversation(sid: str):
    s = load_session_or_404(sid)
    data = s.read_artifact("design_conversation.json")
    return data if isinstance(data, list) else []


@router.patch("/sessions/{sid}/design/enclosure")
async def patch_enclosure(sid: str, request: Request):
    body = await request.json()
    s = load_session_or_404(sid)
    data = s.read_artifact("design.json")
    if data is None:
        raise HTTPException(404, "No design yet")

    enc = data.setdefault("enclosure", {})
    for key in ("height_mm", "top_surface", "edge_top", "edge_bottom"):
        if key in body:
            enc[key] = body[key]

    s.write_artifact("design.json", data)
    cat = get_catalog()
    enrich_components(data.get("ui_placements", []), cat)
    enrich_design_3d(data)
    return data


@router.post("/sessions/{sid}/design/validate-ui-placement")
async def validate_ui_placement(sid: str, request: Request):
    body = await request.json()
    s = load_session_or_404(sid)
    data = s.read_artifact("design.json")
    if data is None:
        raise HTTPException(404, "No design yet")

    cat = get_catalog()
    cat_map = {c.id: c for c in cat.components}
    errors: list[str] = []

    instance_id = body.get("instance_id", "")
    x_mm = body.get("x_mm", 0)
    y_mm = body.get("y_mm", 0)
    edge_index = body.get("edge_index")

    comp_entry = next(
        (c for c in data.get("ui_placements", []) if c["instance_id"] == instance_id),
        None,
    )
    cat_comp = cat_map.get(comp_entry["catalog_id"]) if comp_entry else None

    outline = data.get("outline", [])
    if len(outline) < 3:
        return {"valid": False, "errors": ["Outline has fewer than 3 vertices"]}

    try:
        from shapely.geometry import Polygon, Point
        verts = [(p["x"], p["y"]) for p in outline]
        poly = Polygon(verts)

        if edge_index is not None:
            if edge_index < 0 or edge_index >= len(outline):
                errors.append(f"edge_index {edge_index} out of range")
        else:
            pt = Point(x_mm, y_mm)
            if not poly.contains(pt):
                errors.append("Position is outside the outline")
            elif cat_comp:
                body_c = cat_comp.body
                half_size = max(
                    body_c.width_mm or 0,
                    body_c.length_mm or 0,
                    body_c.diameter_mm or 0,
                ) / 2
                required_clearance = half_size + cat_comp.mounting.keepout_margin_mm
                dist_to_edge = poly.boundary.distance(pt)
                if dist_to_edge < required_clearance:
                    errors.append(
                        f"Too close to edge ({dist_to_edge:.1f}mm, "
                        f"needs {required_clearance:.1f}mm)"
                    )

        if cat_comp:
            body_c = cat_comp.body
            hw = max(body_c.width_mm or 0, body_c.diameter_mm or 0) / 2
            hh = max(body_c.length_mm or 0, body_c.diameter_mm or 0) / 2
            keepout = cat_comp.mounting.keepout_margin_mm

            for other_up in data.get("ui_placements", []):
                if other_up["instance_id"] == instance_id:
                    continue
                other_comp = next(
                    (c for c in data.get("ui_placements", [])
                     if c["instance_id"] == other_up["instance_id"]),
                    None,
                )
                if not other_comp:
                    continue
                other_cat = cat_map.get(other_comp["catalog_id"])
                if not other_cat:
                    continue
                o_body = other_cat.body
                o_hw = max(o_body.width_mm or 0, o_body.diameter_mm or 0) / 2
                o_hh = max(o_body.length_mm or 0, o_body.diameter_mm or 0) / 2
                o_keepout = other_cat.mounting.keepout_margin_mm
                gap_x = abs(x_mm - other_up["x_mm"]) - hw - o_hw
                gap_y = abs(y_mm - other_up["y_mm"]) - hh - o_hh
                gap = max(gap_x, gap_y)
                required_gap = max(keepout, o_keepout, 1.0)
                if gap < required_gap:
                    errors.append(
                        f"Overlaps with {other_up['instance_id']} "
                        f"(gap {gap:.1f}mm, needs {required_gap:.1f}mm)"
                    )
    except ImportError:
        pass

    return {"valid": len(errors) == 0, "errors": errors}


@router.patch("/sessions/{sid}/design/conversation/submit")
async def submit_design_to_conversation(sid: str, request: Request):
    body = await request.json()
    s = load_session_or_404(sid)
    conversation = s.read_artifact("design_conversation.json")
    if not isinstance(conversation, list):
        conversation = []

    design = body.get("design", {})

    new_msg = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": json.dumps({
                    "source": "interactive_designer",
                    "description": "User modified the design interactively in the UI designer. The design below reflects their changes.",
                    "design": design,
                }),
            }
        ],
    }

    if (conversation
            and conversation[-1].get("role") == "user"
            and isinstance(conversation[-1].get("content"), list)
            and any(
                b.get("type") == "text"
                and "interactive_designer" in (b.get("text") or "")
                for b in conversation[-1]["content"]
            )):
        conversation[-1] = new_msg
    else:
        conversation.append(new_msg)

    s.write_artifact("design_conversation.json", conversation)
    return {"ok": True}


@router.get("/sessions/{sid}/design/tokens")
def get_design_tokens(sid: str):
    s = load_session_or_404(sid)
    conversation = s.read_artifact("design_conversation.json")
    if not conversation or not isinstance(conversation, list):
        return {"input_tokens": 0, "budget": TOKEN_BUDGET}

    cat = get_catalog()
    system = build_design_prompt(cat, printer=get_printer(s.printer_id))
    pruned = prune_messages(conversation)
    client = anthropic.Anthropic()
    try:
        result = client.messages.count_tokens(
            model=MODEL,
            messages=pruned,
            system=system,
            tools=DESIGN_TOOLS,
            thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
        )
        return {"input_tokens": result.input_tokens, "budget": TOKEN_BUDGET}
    except Exception:
        return {"input_tokens": 0, "budget": TOKEN_BUDGET}
