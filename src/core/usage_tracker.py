"""
Usage Tracker - Tracks API calls, tokens, and costs for the manufacturing pipeline.

Provides:
- Per-call token counting (input/output)
- Accumulated totals across pipeline stages
- Cost estimation based on Gemini pricing
- Summary report generation
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
import json
from pathlib import Path


# Gemini pricing per 1M tokens (as of 2024/2025)
# https://ai.google.dev/pricing
GEMINI_PRICING = {
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash-8b": {"input": 0.0375, "output": 0.15},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-flash-latest": {"input": 0.10, "output": 0.40},  # Assumed same as 2.0-flash
    # Default fallback
    "default": {"input": 0.10, "output": 0.40}
}


@dataclass
class APICall:
    """Record of a single API call."""
    timestamp: str
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    success: bool
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "stage": self.stage,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "input_cost_usd": round(self.input_cost_usd, 6),
            "output_cost_usd": round(self.output_cost_usd, 6),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "success": self.success,
            "error": self.error
        }


@dataclass  
class UsageTracker:
    """
    Tracks all API usage across a pipeline run.
    
    Usage:
        tracker = UsageTracker()
        tracker.record_call("consultant", "gemini-1.5-flash", 1500, 800, True)
        tracker.record_call("pcb_agent", "gemini-1.5-flash", 2000, 1200, True)
        report = tracker.generate_report()
    """
    calls: List[APICall] = field(default_factory=list)
    pipeline_start: Optional[str] = None
    pipeline_end: Optional[str] = None
    
    def start_pipeline(self) -> None:
        """Mark pipeline start time."""
        self.pipeline_start = datetime.now().isoformat()
        self.calls = []
    
    def end_pipeline(self) -> None:
        """Mark pipeline end time."""
        self.pipeline_end = datetime.now().isoformat()
    
    def record_call(
        self,
        stage: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        success: bool,
        error: Optional[str] = None
    ) -> APICall:
        """
        Record an API call with token counts.
        
        Args:
            stage: Pipeline stage (e.g., "consultant", "pcb_agent")
            model: Model name (e.g., "gemini-1.5-flash")
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            success: Whether the call succeeded
            error: Error message if failed
        
        Returns:
            The recorded APICall object
        """
        # Get pricing for model
        pricing = GEMINI_PRICING.get(model, GEMINI_PRICING["default"])
        
        # Calculate costs (pricing is per 1M tokens)
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        total_cost = input_cost + output_cost
        
        call = APICall(
            timestamp=datetime.now().isoformat(),
            stage=stage,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            total_cost_usd=total_cost,
            success=success,
            error=error
        )
        
        self.calls.append(call)
        return call
    
    @property
    def total_calls(self) -> int:
        """Total number of API calls."""
        return len(self.calls)
    
    @property
    def successful_calls(self) -> int:
        """Number of successful calls."""
        return sum(1 for c in self.calls if c.success)
    
    @property
    def failed_calls(self) -> int:
        """Number of failed calls."""
        return sum(1 for c in self.calls if not c.success)
    
    @property
    def total_input_tokens(self) -> int:
        """Total input tokens across all calls."""
        return sum(c.input_tokens for c in self.calls)
    
    @property
    def total_output_tokens(self) -> int:
        """Total output tokens across all calls."""
        return sum(c.output_tokens for c in self.calls)
    
    @property
    def total_tokens(self) -> int:
        """Total tokens (input + output) across all calls."""
        return self.total_input_tokens + self.total_output_tokens
    
    @property
    def total_cost_usd(self) -> float:
        """Total cost in USD across all calls."""
        return sum(c.total_cost_usd for c in self.calls)
    
    @property
    def total_input_cost_usd(self) -> float:
        """Total input cost in USD."""
        return sum(c.input_cost_usd for c in self.calls)
    
    @property
    def total_output_cost_usd(self) -> float:
        """Total output cost in USD."""
        return sum(c.output_cost_usd for c in self.calls)
    
    def get_calls_by_stage(self) -> dict:
        """Group calls by pipeline stage."""
        by_stage = {}
        for call in self.calls:
            if call.stage not in by_stage:
                by_stage[call.stage] = []
            by_stage[call.stage].append(call)
        return by_stage
    
    def get_stage_summary(self) -> dict:
        """Get token/cost summary per stage."""
        by_stage = self.get_calls_by_stage()
        summary = {}
        
        for stage, calls in by_stage.items():
            summary[stage] = {
                "calls": len(calls),
                "input_tokens": sum(c.input_tokens for c in calls),
                "output_tokens": sum(c.output_tokens for c in calls),
                "total_tokens": sum(c.total_tokens for c in calls),
                "total_cost_usd": round(sum(c.total_cost_usd for c in calls), 6)
            }
        
        return summary
    
    def generate_report(self) -> dict:
        """
        Generate a comprehensive usage report.
        
        Returns:
            dict with full usage statistics and breakdown
        """
        return {
            "summary": {
                "pipeline_start": self.pipeline_start,
                "pipeline_end": self.pipeline_end,
                "total_api_calls": self.total_calls,
                "successful_calls": self.successful_calls,
                "failed_calls": self.failed_calls,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_tokens": self.total_tokens,
                "total_input_cost_usd": round(self.total_input_cost_usd, 6),
                "total_output_cost_usd": round(self.total_output_cost_usd, 6),
                "total_cost_usd": round(self.total_cost_usd, 6)
            },
            "by_stage": self.get_stage_summary(),
            "calls": [call.to_dict() for call in self.calls],
            "pricing_reference": {
                "note": "Costs calculated based on Gemini API pricing",
                "rates_per_1M_tokens": GEMINI_PRICING
            }
        }
    
    def generate_markdown_report(self) -> str:
        """
        Generate a human-readable markdown report.
        
        Returns:
            Formatted markdown string
        """
        report = self.generate_report()
        summary = report["summary"]
        by_stage = report["by_stage"]
        
        lines = [
            "# 📊 Pipeline Cost & Usage Report",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| **Total API Calls** | {summary['total_api_calls']} |",
            f"| Successful | {summary['successful_calls']} |",
            f"| Failed | {summary['failed_calls']} |",
            f"| **Total Tokens** | {summary['total_tokens']:,} |",
            f"| Input Tokens | {summary['total_input_tokens']:,} |",
            f"| Output Tokens | {summary['total_output_tokens']:,} |",
            f"| **Total Cost** | ${summary['total_cost_usd']:.6f} |",
            f"| Input Cost | ${summary['total_input_cost_usd']:.6f} |",
            f"| Output Cost | ${summary['total_output_cost_usd']:.6f} |",
            "",
            "## Cost Breakdown by Stage",
            "",
            "| Stage | Calls | Input Tokens | Output Tokens | Total Tokens | Cost (USD) |",
            "|-------|-------|--------------|---------------|--------------|------------|",
        ]
        
        for stage, stats in by_stage.items():
            lines.append(
                f"| {stage} | {stats['calls']} | {stats['input_tokens']:,} | "
                f"{stats['output_tokens']:,} | {stats['total_tokens']:,} | "
                f"${stats['total_cost_usd']:.6f} |"
            )
        
        lines.extend([
            "",
            "## Individual API Calls",
            "",
        ])
        
        for i, call in enumerate(report["calls"], 1):
            status = "✅" if call["success"] else "❌"
            lines.append(f"### Call {i}: {call['stage']} {status}")
            lines.append(f"- **Model**: {call['model']}")
            lines.append(f"- **Tokens**: {call['input_tokens']:,} in / {call['output_tokens']:,} out = {call['total_tokens']:,} total")
            lines.append(f"- **Cost**: ${call['total_cost_usd']:.6f}")
            lines.append(f"- **Time**: {call['timestamp']}")
            if call["error"]:
                lines.append(f"- **Error**: {call['error']}")
            lines.append("")
        
        lines.extend([
            "---",
            "",
            "## Pricing Reference",
            "",
            "Costs calculated using Gemini API pricing (per 1M tokens):",
            "",
            "| Model | Input | Output |",
            "|-------|-------|--------|",
        ])
        
        for model, prices in GEMINI_PRICING.items():
            if model != "default":
                lines.append(f"| {model} | ${prices['input']:.4f} | ${prices['output']:.4f} |")
        
        return "\n".join(lines)
    
    def save_report(self, output_dir: Path) -> tuple[Path, Path]:
        """
        Save both JSON and Markdown reports to the output directory.
        
        Args:
            output_dir: Directory to save reports
        
        Returns:
            Tuple of (json_path, markdown_path)
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save JSON report
        json_path = output_dir / "usage_report.json"
        json_path.write_text(
            json.dumps(self.generate_report(), indent=2),
            encoding="utf-8"
        )
        
        # Save Markdown report
        md_path = output_dir / "usage_report.md"
        md_path.write_text(self.generate_markdown_report(), encoding="utf-8")
        
        return json_path, md_path


# Global tracker instance for easy access across modules
_global_tracker: Optional[UsageTracker] = None


def get_tracker() -> UsageTracker:
    """Get the global usage tracker instance."""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = UsageTracker()
    return _global_tracker


def reset_tracker() -> UsageTracker:
    """Reset and return a new global tracker instance."""
    global _global_tracker
    _global_tracker = UsageTracker()
    return _global_tracker
