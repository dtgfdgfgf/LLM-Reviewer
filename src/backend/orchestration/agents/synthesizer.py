"""Synthesizer agent — final report on three independent reviews."""

from backend.orchestration.agents.base import (
    WATCHDOG_POLL_S,
    BaseAgent,
)
from backend.orchestration.model_router import AgentRole

# Synthesizer is a single-turn call with no tools, so it should respond
# faster than reviewers.  Keep tighter liveness and total bounds.
SYNTH_TOTAL_TIMEOUT_S: float = 300.0  # 5-min hard ceiling
SYNTH_LIVENESS_TIMEOUT_S: float = 60.0  # 60 s idle → stuck

SYSTEM_PROMPT = """You are a staff engineer writing the final review report.

You will receive three independent reviews of the same input. Each reviewer covered
the same material
from a different angle.

**Your job is to write the final report. Nothing else.**
- You do not ask what to do next.
- You do not offer to examine more files or explore further.
- You do not end with "Would you like me to…" or "Let me know if…"
- You read the reviews, exercise judgment, and write the report. Full stop.

Your job is NOT to copy-paste or lightly summarize their findings. Your job is to:
- **Exercise judgment**: decide what actually matters and what doesn't. Reviewers can be wrong.
- **Resolve conflicts**: if reviewers disagree, pick a side and explain why. Don't hedge.
- **Find signal**: surface the 3-5 things the team must act on. A long list is a failure of
  judgment, not thoroughness.
- **Name patterns**: issues appearing across multiple reviewers indicate systemic problems —
  label them as such.
- **Choose the structure**: adapt the report structure to the input.
  Small file reviews can be compact.
  Broader folder reviews can use clearer sections. Markdown is preferred.
- **Stay useful**: prioritize concrete problems, actionable fixes, and strengths worth preserving.

---
Be a decision-maker, not a transcriptionist.
Do not force a pass/fail verdict or rigid template unless the input clearly calls for it.
Every sentence earns its place. Do NOT end with questions, offers, or next-step prompts.
All outward-facing output must be written in Traditional Chinese.
Use concise Markdown headings and body copy in zh-TW.
"""


class SynthesizerAgent(BaseAgent):
    role = AgentRole.SYNTHESIZER

    def _build_prompt(self, files: list[str], focus: str) -> str:
        review_1 = files[0] if len(files) > 0 else "[not available]"
        review_2 = files[1] if len(files) > 1 else "[not available]"
        review_3 = files[2] if len(files) > 2 else "[not available]"

        return (
            f"Write the final report for the following three independent reviews.\n\n"
            f"Review focus: {focus}\n\n"
            f"---\n## REVIEWER 1\n{review_1}\n\n"
            f"---\n## REVIEWER 2\n{review_2}\n\n"
            f"---\n## REVIEWER 3\n{review_3}\n\n"
            f"---\n"
            f"Now produce the final review report. Exercise judgment — don't just aggregate."
        )

    async def run(self, files: list[str], focus: str) -> str:
        """
        Override run() for the synthesizer — it does not use file tools.

        files contains the three review texts (not file paths).
        Uses the same hybrid timeout as BaseAgent (hard ceiling + liveness
        watchdog), but with tighter bounds since this is a single-turn call.
        """
        import asyncio
        import time
        from typing import Any

        start_time = time.monotonic()
        self._started_at_ms = int(time.time() * 1000)
        self._completed_at_ms = None
        self._status = "running"
        self._tool_call_count = 0
        self._last_activity = start_time
        self._log.info("Synthesizer starting")

        await self._publish(
            {"type": "agent.started", "agent": self.role.value, "model": self._model}
        )

        unsubscribe = self._session.on(self._handle_sdk_event)
        try:
            prompt = self._build_prompt(files, focus)

            async def _run_session() -> Any:
                return await self._session.send_and_wait(
                    {"prompt": prompt}, timeout=SYNTH_TOTAL_TIMEOUT_S
                )

            async def _watchdog() -> str:
                deadline = start_time + SYNTH_TOTAL_TIMEOUT_S
                while True:
                    await asyncio.sleep(WATCHDOG_POLL_S)
                    now = time.monotonic()
                    if now >= deadline:
                        return "total"
                    if now - self._last_activity > SYNTH_LIVENESS_TIMEOUT_S:
                        return "liveness"

            session_task = asyncio.create_task(_run_session())
            watchdog_task = asyncio.create_task(_watchdog())

            done, pending = await asyncio.wait(
                [session_task, watchdog_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            if session_task not in done:
                reason = watchdog_task.result()
                elapsed = int(time.monotonic() - start_time)
                idle = int(time.monotonic() - self._last_activity)
                if reason == "liveness":
                    raise TimeoutError(
                        f"No activity for {idle}s (elapsed {elapsed}s) — synthesizer appears stuck"
                    )
                raise TimeoutError(f"Exceeded hard timeout of {int(SYNTH_TOTAL_TIMEOUT_S)}s")

            event = session_task.result()
            result = ""
            if event and event.data.content:
                result = event.data.content

            duration_ms = int((time.monotonic() - start_time) * 1000)
            self._completed_at_ms = int(time.time() * 1000)
            self._status = "complete"
            self._log.info("Synthesizer done", duration_ms=duration_ms)

            await self._publish(
                {
                    "type": "agent.done",
                    "agent": self.role.value,
                    "duration_ms": duration_ms,
                }
            )
            return result

        except TimeoutError as exc:
            msg = f"Synthesizer timed out: {exc}"
            self._completed_at_ms = int(time.time() * 1000)
            self._status = "error"
            self._log.error(msg)
            await self._publish({"type": "agent.error", "agent": self.role.value, "error": msg})
            return "[synthesis timed out]"

        except Exception as exc:
            msg = str(exc)
            self._completed_at_ms = int(time.time() * 1000)
            self._status = "error"
            self._log.error("Synthesizer failed", error=msg, exc_info=True)
            await self._publish({"type": "agent.error", "agent": self.role.value, "error": msg})
            return f"[synthesis failed: {msg}]"

        finally:
            unsubscribe()
            await self._session.destroy()
