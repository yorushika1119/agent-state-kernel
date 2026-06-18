"""Smoke test: Hermes Gateway interrupts a real long-running tool process.

This script reuses the local Hermes Gateway interrupt harness, but swaps in a
controlled agent that starts an actual Python sleep process and then tries to
report the tool result after the user interrupt arrives.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace


HERMES_ROOT = Path(
    os.environ.get(
        "HERMES_AGENT_ROOT",
        r"C:\Users\EDY\AppData\Local\hermes\hermes-agent",
    )
)
HERMES_SCRIPTS = HERMES_ROOT / "scripts"
for path in (HERMES_ROOT, HERMES_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import live_interrupt_demo  # type: ignore  # noqa: E402


class ToolInterruptAgent:
    tool_started = False
    interrupt_received = False
    process_started = False
    process_was_terminated = False
    late_tool_result_attempted = False
    process_return_code: int | None = None
    process_stdout = ""
    process_stderr = ""

    def __init__(self, **kwargs):
        self.model = kwargs.get("model", "tool-smoke-model")
        self.provider = kwargs.get("provider", "tool-smoke-provider")
        self.base_url = kwargs.get("base_url", "")
        self.api_key = kwargs.get("api_key", "")
        self.api_mode = kwargs.get("api_mode", "chat_completions")
        self.session_id = kwargs.get("session_id", "session-tool-smoke")
        self.tools = []
        self.tool_start_callback = None
        self.tool_complete_callback = None
        self._interrupted = False
        self._interrupt_message = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0, context_length=0)

    @property
    def is_interrupted(self) -> bool:
        return self._interrupted

    def interrupt(self, message=None):
        self._interrupted = True
        self._interrupt_message = message
        type(self).interrupt_received = True
        with self._lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()
            type(self).process_was_terminated = True

    def get_activity_summary(self):
        return {
            "api_call_count": 1,
            "max_iterations": 1,
            "current_tool": "terminal",
            "last_activity_ts": time.time(),
            "last_activity_desc": "terminal python sleep",
            "seconds_since_activity": 0.0,
        }

    def _start_sleep_process(self) -> subprocess.Popen:
        script = Path(tempfile.gettempdir()) / "ask_tool_interrupt_sleep.py"
        script.write_text(
            "import time\n"
            "time.sleep(15)\n"
            "print('TOOL_DONE')\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(script),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with self._lock:
            self._process = process
        type(self).process_started = True
        return process

    def run_conversation(self, user_message, conversation_history=None, task_id=None, persist_user_message=None):
        message_text = user_message
        if isinstance(user_message, list):
            message_text = "\n".join(
                str(item.get("text") or "")
                for item in user_message
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()

        if "first long task" not in str(message_text):
            final = f"FINAL:{message_text}"
            return {
                "final_response": final,
                "messages": [
                    {"role": "user", "content": str(message_text)},
                    {"role": "assistant", "content": final},
                ],
                "api_calls": 1,
                "completed": True,
                "interrupted": False,
                "error": "",
            }

        args = {"command": "python sleep 15"}
        if self.tool_start_callback:
            self.tool_start_callback("call_sleep", "terminal", args)
            type(self).tool_started = True

        process = self._start_sleep_process()
        while process.poll() is None:
            if self._interrupted:
                if process.poll() is None:
                    process.terminate()
                    type(self).process_was_terminated = True
                try:
                    stdout, stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=2)
                type(self).process_return_code = process.returncode
                if self.tool_complete_callback:
                    type(self).late_tool_result_attempted = True
                    self.tool_complete_callback(
                        "call_sleep",
                        "terminal",
                        args,
                        f"INTERRUPTED rc={process.returncode} stdout={stdout} stderr={stderr}",
                    )
                return {
                    "final_response": "",
                    "messages": [{"role": "user", "content": str(message_text)}],
                    "api_calls": 1,
                    "completed": False,
                    "interrupted": True,
                    "interrupt_message": self._interrupt_message or "Operation interrupted.",
                    "error": "",
                }
            time.sleep(0.05)

        stdout, stderr = process.communicate(timeout=2)
        type(self).process_stdout = stdout
        type(self).process_stderr = stderr
        type(self).process_return_code = process.returncode
        if self._interrupted:
            if self.tool_complete_callback:
                type(self).late_tool_result_attempted = True
                self.tool_complete_callback(
                    "call_sleep",
                    "terminal",
                    args,
                    f"INTERRUPTED rc={process.returncode} stdout={stdout} stderr={stderr}",
                )
            return {
                "final_response": "",
                "messages": [{"role": "user", "content": str(message_text)}],
                "api_calls": 1,
                "completed": False,
                "interrupted": True,
                "interrupt_message": self._interrupt_message or "Operation interrupted.",
                "error": "",
            }
        if self.tool_complete_callback:
            self.tool_complete_callback("call_sleep", "terminal", args, stdout or stderr)

        final = "FINAL:first long task"
        return {
            "final_response": final,
            "messages": [
                {"role": "user", "content": str(message_text)},
                {"role": "assistant", "content": final},
            ],
            "api_calls": 1,
            "completed": True,
            "interrupted": False,
            "error": "",
        }


async def main() -> None:
    live_interrupt_demo.DemoAgent = ToolInterruptAgent
    await live_interrupt_demo.main(real_model=False, scenario="interrupt")
    print("TOOL_SMOKE_SUMMARY:", flush=True)
    print(f"  tool_started={ToolInterruptAgent.tool_started}", flush=True)
    print(f"  process_started={ToolInterruptAgent.process_started}", flush=True)
    print(f"  interrupt_received={ToolInterruptAgent.interrupt_received}", flush=True)
    print(f"  process_was_terminated={ToolInterruptAgent.process_was_terminated}", flush=True)
    print(f"  late_tool_result_attempted={ToolInterruptAgent.late_tool_result_attempted}", flush=True)
    print(f"  process_return_code={ToolInterruptAgent.process_return_code}", flush=True)
    print(f"  process_stdout={ToolInterruptAgent.process_stdout.strip()}", flush=True)
    print(f"  process_stderr={ToolInterruptAgent.process_stderr.strip()}", flush=True)

    assert ToolInterruptAgent.tool_started
    assert ToolInterruptAgent.process_started
    assert ToolInterruptAgent.interrupt_received
    assert ToolInterruptAgent.process_was_terminated
    assert ToolInterruptAgent.late_tool_result_attempted
    assert ToolInterruptAgent.process_return_code is not None


if __name__ == "__main__":
    asyncio.run(main())
