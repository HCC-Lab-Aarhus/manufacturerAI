from __future__ import annotations
import json
from pathlib import Path
from typing import Optional, Callable

from src.core.state import PipelineState, PipelineContext
from src.core.reporting import write_report
from src.core.usage_tracker import get_tracker, reset_tracker
from src.llm.consultant_agent import ConsultantAgent
from src.pcb_python.pcb_agent import PCBAgent
from src.pcb_python.ts_router_bridge import TSPCBRouter
from src.design.enclosure_agent import Enclosure3DAgent
from src.blender.runner import BlenderRunner

# Progress callback type
ProgressCallback = Callable[[str, int, int, Optional[str]], None]


class Orchestrator:
    """
    Central orchestrator managing the manufacturing pipeline state machine.
    
    Pipeline flow:
    1. COLLECT_REQUIREMENTS: Consultant normalizes user input → design_spec.json
    2. GENERATE_PCB: PCB agent creates layout → pcb_layout.json
    3. CHECK_PCB_FEASIBILITY: Feasibility tool validates → feasibility_report.json
    4. ITERATE_PCB: If fails, apply fixes and retry (max N iterations)
    5. GENERATE_ENCLOSURE: 3D agent builds enclosure → STL files
    6. DONE
    """
    
    def __init__(self, blender_bin: str | None = None, max_iterations: int = 5, use_parametric: bool = True):
        self.blender = BlenderRunner(blender_bin=blender_bin)
        self.enclosure_agent = Enclosure3DAgent()
        self.consultant = ConsultantAgent()
        self.pcb_agent = PCBAgent()
        self.router = TSPCBRouter()
        self.max_iterations = max_iterations
        self.use_parametric = use_parametric
        self._progress_callback: Optional[ProgressCallback] = None

    def set_progress_callback(self, callback: ProgressCallback) -> None:
        """Set a callback function to receive progress updates."""
        self._progress_callback = callback
    
    def _report_progress(self, stage: str, iteration: int, max_iter: int, message: Optional[str] = None) -> None:
        """Report progress to callback if set."""
        if self._progress_callback:
            self._progress_callback(stage, iteration, max_iter, message)

    def run_from_prompt(
        self, 
        text: str, 
        out_dir: Path, 
        use_llm: bool = True,
        previous_design: dict | None = None
    ) -> dict:
        """
        Execute full pipeline from user prompt.
        
        Args:
            text: User prompt
            out_dir: Output directory for run files
            use_llm: Whether to use LLM for design spec generation
            previous_design: If provided, treat as modification of existing design
        
        Returns:
            The generated design_spec dict
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize usage tracker for this pipeline run
        tracker = reset_tracker()
        tracker.start_pipeline()
        
        context = PipelineContext(
            run_dir=out_dir,
            max_iterations=self.max_iterations
        )
        context.previous_design = previous_design  # Store for use in requirements collection
        
        state = PipelineState.COLLECT_REQUIREMENTS
        
        while state != PipelineState.DONE and state != PipelineState.ERROR:
            if state == PipelineState.COLLECT_REQUIREMENTS:
                state = self._collect_requirements(context, text, use_llm)
            
            elif state == PipelineState.GENERATE_PCB:
                state = self._generate_pcb(context)
            
            elif state == PipelineState.CHECK_PCB_FEASIBILITY:
                state = self._check_feasibility(context)
            
            elif state == PipelineState.ITERATE_PCB:
                state = self._iterate_pcb(context)
            
            elif state == PipelineState.GENERATE_ENCLOSURE:
                state = self._generate_enclosure(context)
            
            elif state == PipelineState.FINAL_VERIFY:
                state = self._final_verify(context)
        
        # End usage tracking and save report
        tracker.end_pipeline()
        try:
            json_path, md_path = tracker.save_report(out_dir)
            print(f"\n[ORCHESTRATOR] 📊 Usage report saved:")
            print(f"  - {json_path}")
            print(f"  - {md_path}")
            print(f"  - Total API calls: {tracker.total_calls}")
            print(f"  - Total tokens: {tracker.total_tokens:,}")
            print(f"  - Estimated cost: ${tracker.total_cost_usd:.6f}")
        except Exception as e:
            print(f"[ORCHESTRATOR] ⚠ Failed to save usage report: {e}")
        
        if state == PipelineState.ERROR:
            raise RuntimeError("Pipeline failed. Check logs in run directory.")
        
        return context.design_spec
    
    def _collect_requirements(self, context: PipelineContext, user_prompt: str, use_llm: bool) -> PipelineState:
        """Stage 1: Consultant agent normalizes requirements."""
        print("\n" + "-"*60)
        print("[ORCHESTRATOR] Stage 1: Collecting requirements...")
        print("-"*60)
        print(f"[ORCHESTRATOR] use_llm: {use_llm}")
        self._report_progress("COLLECT_REQUIREMENTS", 0, self.max_iterations, "Normalizing user requirements")
        
        # Pass previous design if available for modification mode
        previous = getattr(context, 'previous_design', None)
        if previous:
            print("[ORCHESTRATOR] PATH: MODIFICATION mode - previous design provided")
        else:
            print("[ORCHESTRATOR] PATH: NEW DESIGN mode - no previous design")
        
        context.design_spec = self.consultant.generate_design_spec(
            user_prompt, 
            use_llm=use_llm,
            previous_design=previous
        )
        context.save_design_spec()
        
        button_count = len(context.design_spec.get('buttons', []))
        print(f"[ORCHESTRATOR] ✓ Design spec created with {button_count} buttons")
        self._report_progress("COLLECT_REQUIREMENTS", 0, self.max_iterations, f"Created spec with {button_count} buttons")
        return PipelineState.GENERATE_PCB
    
    def _generate_pcb(self, context: PipelineContext) -> PipelineState:
        """Stage 2: PCB agent creates layout."""
        print("\n" + "-"*60)
        print(f"[ORCHESTRATOR] Stage 2: Generating PCB layout (iteration {context.iteration + 1})...")
        print("-"*60)
        self._report_progress("GENERATE_PCB", context.iteration + 1, self.max_iterations, "Creating PCB layout")
        
        context.iteration += 1
        
        # If we have a previous feasibility report, pass it for fixes
        previous_report = context.feasibility_report if context.iteration > 1 else None
        if previous_report:
            print("[ORCHESTRATOR] PATH: ITERATION mode - applying fixes from previous report")
        else:
            print("[ORCHESTRATOR] PATH: INITIAL layout generation")
        
        context.pcb_layout = self.pcb_agent.generate_layout(
            context.design_spec,
            previous_feasibility_report=previous_report
        )
        context.save_pcb_layout()
        
        # Route and generate debug images via TypeScript CLI
        print("[ORCHESTRATOR] Routing traces and generating debug images via TypeScript...")
        try:
            context.routing_result = self.router.route(context.pcb_layout, context.run_dir)
            context.save_routing_result()
            if context.routing_result.get("success"):
                print(f"[ORCHESTRATOR] ✓ Routing completed with {len(context.routing_result.get('traces', []))} traces")
            else:
                failed = context.routing_result.get("failed_nets", [])
                print(f"[ORCHESTRATOR] ⚠ Routing had {len(failed)} failed nets")
        except Exception as e:
            print(f"[ORCHESTRATOR] ✗ Routing failed: {e}")
            context.routing_result = {"success": False, "traces": [], "failed_nets": [], "error": str(e)}
        
        print(f"[ORCHESTRATOR] ✓ PCB layout v{context.iteration} created")
        self._report_progress("GENERATE_PCB", context.iteration, self.max_iterations, f"PCB layout v{context.iteration} created")
        return PipelineState.CHECK_PCB_FEASIBILITY
    
    def _check_feasibility(self, context: PipelineContext) -> PipelineState:
        """Stage 3: Check routing feasibility via TypeScript router results."""
        print("Stage 3: Checking feasibility...")
        self._report_progress("CHECK_FEASIBILITY", context.iteration, self.max_iterations, "Running DRC checks")
        
        # Use routing result from _generate_pcb (already routed)
        routing_result = context.routing_result or {"success": False, "traces": [], "failed_nets": []}
        
        # Convert routing result to feasibility report format
        errors = []
        for net in routing_result.get("failed_nets", []):
            errors.append({
                "type": "routing_failed",
                "net": net,
                "message": f"Failed to route net: {net}"
            })
        
        if routing_result.get("error"):
            errors.append({
                "type": "router_error",
                "message": routing_result["error"]
            })
        
        context.feasibility_report = {
            "feasible": routing_result.get("success", False) and len(errors) == 0,
            "errors": errors
        }
        
        context.save_feasibility_report()
        
        if context.feasibility_report["feasible"]:
            print("  ✓ Feasibility check passed")
            self._report_progress("CHECK_FEASIBILITY", context.iteration, self.max_iterations, "All checks passed!")
            return PipelineState.GENERATE_ENCLOSURE
        else:
            error_count = len(context.feasibility_report["errors"])
            print(f"  ✗ Feasibility check failed with {error_count} errors")
            self._report_progress("CHECK_FEASIBILITY", context.iteration, self.max_iterations, 
                                  f"Failed with {error_count} errors - iterating")
            
            if context.iteration >= context.max_iterations:
                print(f"  ✗ Max iterations ({context.max_iterations}) reached. Giving up.")
                self._report_progress("ERROR", context.iteration, self.max_iterations, 
                                      f"Max iterations ({context.max_iterations}) reached")
                return PipelineState.ERROR
            
            return PipelineState.ITERATE_PCB
    
    def _iterate_pcb(self, context: PipelineContext) -> PipelineState:
        """Stage 4: Apply fixes and regenerate PCB."""
        print(f"Stage 4: Iterating PCB (attempt {context.iteration + 1}/{context.max_iterations})...")
        self._report_progress("ITERATE_PCB", context.iteration + 1, self.max_iterations, 
                              "Applying fixes from feasibility report")
        
        # The PCB agent will read the feasibility report and apply fixes
        return PipelineState.GENERATE_PCB
    
    def _generate_enclosure(self, context: PipelineContext) -> PipelineState:
        """Stage 5: Generate 3D enclosure from PCB layout."""
        print("\n" + "-"*60)
        print("[ORCHESTRATOR] Stage 5: Generating enclosure...")
        print("-"*60)
        print(f"[ORCHESTRATOR] use_parametric: {self.use_parametric}")
        self._report_progress("GENERATE_ENCLOSURE", context.iteration, self.max_iterations, 
                              "Creating 3D model")
        
        if self.use_parametric:
            print("[ORCHESTRATOR] PATH: Parametric enclosure (OpenSCAD)")
            # Use new parametric 3D agent that reads pcb_layout.json directly
            # Pass routing result for trace channels in bottom shell
            outputs = self.enclosure_agent.generate_from_pcb_layout(
                pcb_layout=context.pcb_layout,
                design_spec=context.design_spec,
                output_dir=context.run_dir,
                routing_result=context.routing_result
            )
            print(f"[ORCHESTRATOR] ✓ Parametric enclosure generated: {list(outputs.keys())}")
        else:
            print("[ORCHESTRATOR] PATH: FALLBACK → Legacy Blender workflow")
            # Fallback to legacy Blender runner
            params_for_blender = self._convert_pcb_to_legacy_params(context.pcb_layout, context.design_spec)
            params_path = context.run_dir / "params_for_blender.json"
            params_path.write_text(json.dumps(params_for_blender, indent=2), encoding="utf-8")
            self.blender.generate_stls(params_path=params_path, out_dir=context.run_dir)
            print("[ORCHESTRATOR] ✓ Blender enclosure generated")
        
        # Write final report
        write_report(
            out_dir=context.run_dir,
            design_spec=context.design_spec,
            pcb_layout=context.pcb_layout,
            feasibility_report=context.feasibility_report,
            iterations=context.iteration
        )
        
        self._report_progress("DONE", context.iteration, self.max_iterations, "Complete!")
        return PipelineState.DONE
    
    def _final_verify(self, context: PipelineContext) -> PipelineState:
        """Stage 6: Optional final verification."""
        print("Stage 6: Final verification...")
        # TODO: Add enclosure printability checks
        return PipelineState.DONE
    
    def _convert_pcb_to_legacy_params(self, pcb_layout: dict, design_spec: dict) -> dict:
        """
        Temporary converter from new pcb_layout.json to old params format for Blender.
        TODO: Replace Blender script to read pcb_layout.json directly.
        """
        # Extract board dimensions from outline
        outline = pcb_layout["board"]["outline_polygon"]
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        board_width = max(xs) - min(xs)
        board_length = max(ys) - min(ys)
        
        # Count buttons
        buttons = [c for c in pcb_layout["components"] if c["type"] == "button"]
        
        # Estimate grid layout (simplified)
        button_count = len(buttons)
        
        # Use design spec for remaining params
        device = design_spec["device_constraints"]
        constraints = design_spec["constraints"]
        
        return {
            "remote": {
                "length_mm": device["length_mm"],
                "width_mm": device["width_mm"],
                "thickness_mm": device["thickness_mm"],
                "wall_mm": constraints["min_wall_thickness_mm"],
                "corner_radius_mm": 6.0
            },
            "buttons": {
                "button_count": button_count,
                "diam_mm": design_spec["buttons"][0]["cap_diameter_mm"] if design_spec["buttons"] else 9.0,
                "spacing_mm": constraints["min_button_spacing_mm"],
                "margin_top_mm": 20.0,
                "margin_bottom_mm": 18.0,
                "margin_side_mm": constraints["edge_clearance_mm"],
                "hole_clearance_mm": 0.25
            }
        }

    # Legacy methods for backward compatibility
    def run_from_params_file(self, params_path: Path, out_dir: Path) -> None:
        """Legacy method - kept for backward compatibility."""
        # TODO: Convert this to use new pipeline or deprecate
        from src.design.models import RemoteParams
        from src.design.validators import validate_and_fix_params
        
        params_raw = json.loads(params_path.read_text(encoding="utf-8"))
        (out_dir / "params_raw.json").write_text(json.dumps(params_raw, indent=2), encoding="utf-8")

        fixed, issues = validate_and_fix_params(params_raw)
        (out_dir / "params_validated.json").write_text(json.dumps(fixed, indent=2), encoding="utf-8")

        params = RemoteParams.model_validate(fixed)
        self.blender.generate_stls(params_path=(out_dir / "params_validated.json"), out_dir=out_dir)
