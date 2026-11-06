import logging
import pathlib
import unittest
from unittest import mock

from modules.engine import git_operations


class CommitMessageRoutingTests(unittest.TestCase):
    def make_namespace(self, **overrides):
        namespace = {
            "ROOT": pathlib.Path("/project"),
            "PROVIDER": "default-provider",
            "MODEL": "default-model",
            "LOGGER": logging.getLogger("test.commit_message_routing"),
            "load_config": lambda: {"commit_message": {
                "enabled": True,
                "prompt": "Review these changes:\n{diff_section}",
            }},
            "DEFAULT_COMMIT_USER_PROMPT": "Review these changes:\n{diff_section}",
            "DEFAULT_COMMIT_SYSTEM_PROMPT": "Return only a concise commit message.",
            "DEFAULT_COMMIT_DIFF_SECTION_WITH_DIFF": "Files: {changes}\nDiff:\n{diff}",
            "DEFAULT_COMMIT_DIFF_SECTION_WITHOUT_DIFF": "Files: {changes}",
            "api_request_for_role": mock.Mock(return_value={
                "choices": [{"message": {"content": "docs: route command commits"}}]
            }),
        }
        namespace.update(overrides)
        git_operations.install(namespace)
        return namespace

    def test_llm_commit_generation_uses_shared_commit_message_role_router(self):
        api_request = mock.Mock(return_value={
            "choices": [{"message": {"content": "docs: route command commits"}}]
        })
        namespace = self.make_namespace(api_request_for_role=api_request)

        result = namespace["generate_commit_message_llm"](
            "docs/MODEL_ROUTING.md", "diff --git a/docs/MODEL_ROUTING.md"
        )

        self.assertEqual(result, "docs: route command commits")
        api_request.assert_called_once()
        messages, role = api_request.call_args.args
        self.assertEqual(role, "commit_message")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("diff --git", messages[1]["content"])

    def test_message_only_command_path_invokes_message_generator(self):
        command = mock.Mock(side_effect=[
            {"stdout": "", "stderr": "", "returncode": 0, "timed_out": False},
            {"stdout": "", "stderr": "", "returncode": 0, "timed_out": False},
        ])
        namespace = self.make_namespace(run_interruptible_command=command)
        generator = mock.Mock(return_value="chore: generated through role router")
        namespace["generate_commit_message"] = generator

        result = namespace["git_commit"](message_only=True)

        self.assertEqual(result, "chore: generated through role router")
        generator.assert_called_once_with()

    def test_explicit_commit_message_bypasses_model_generation(self):
        command = mock.Mock(side_effect=[
            {"stdout": "", "stderr": "", "returncode": 0, "timed_out": False},
            {"stdout": "", "stderr": "", "returncode": 0, "timed_out": False},
        ])
        namespace = self.make_namespace(run_interruptible_command=command)
        generator = mock.Mock()
        namespace["generate_commit_message"] = generator

        result = namespace["git_commit"]("manual: supplied message", message_only=True)

        self.assertEqual(result, "manual: supplied message")
        generator.assert_not_called()

    def test_anthropic_style_response_is_supported(self):
        namespace = self.make_namespace(api_request_for_role=mock.Mock(return_value={
            "content": [
                {"type": "thinking", "thinking": "private"},
                {"type": "text", "text": "fix: handle routed response"},
            ]
        }))
        self.assertEqual(
            namespace["generate_commit_message_llm"]("src/app.py"),
            "fix: handle routed response",
        )

    def test_disabled_llm_does_not_call_router(self):
        api_request = mock.Mock()
        namespace = self.make_namespace(
            load_config=lambda: {"commit_message": {"enabled": False}},
            api_request_for_role=api_request,
        )
        self.assertIsNone(namespace["generate_commit_message_llm"]("src/app.py"))
        api_request.assert_not_called()

    def test_failed_routed_request_returns_to_pattern_fallback(self):
        namespace = self.make_namespace(api_request_for_role=mock.Mock(
            side_effect=RuntimeError("provider unavailable")
        ))
        self.assertIsNone(namespace["generate_commit_message_llm"]("src/app.py"))


if __name__ == "__main__":
    unittest.main()
