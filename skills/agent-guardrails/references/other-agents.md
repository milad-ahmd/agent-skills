# Using the guardrails hook outside Claude Code

`install` only writes Claude Code settings. For the agents below, copy `scripts/guardrails.py` into the project yourself and register it by hand. The formats here were checked against each vendor's hooks documentation (Cursor: cursor.com/docs/agent/hooks, Codex: the Codex hooks page). Re-check them if the agent has had a major release since.

## Cursor (`beforeShellExecution`)

Copy the script to `.cursor/hooks/guardrails.py`, then create or extend `.cursor/hooks.json`:

```json
{
  "version": 1,
  "hooks": {
    "beforeShellExecution": [
      { "command": "python3 .cursor/hooks/guardrails.py hook --format cursor" }
    ]
  }
}
```

- Project hooks run from the project root, so the path is relative to it. For a user-level hook in `~/.cursor/hooks.json`, hooks run from `~/.cursor/`, so use `python3 ./hooks/guardrails.py hook --format cursor`.
- Cursor sends `{"command": ..., "cwd": ...}` on stdin. With `--format cursor` the script always answers `{"permission": "allow" | "ask" | "deny"}` and adds `user_message` and `agent_message` for ask and deny. Cursor treats invalid output from a permission hook as a block, which is why the script prints `allow` instead of nothing.
- Not documented by Cursor: whether a hook's `allow` skips Cursor's own command approval. If the user relies on Cursor's approval prompts, test one harmless command after installing.
- When several hooks answer, Cursor applies deny over ask over allow.
- There is no project-root variable here, so `rm-outside-project` treats the command's `cwd` as the project.

## Codex (`PreToolUse`, matcher `Bash`)

Copy the script to `.codex/hooks/guardrails.py`, then create or extend `.codex/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$(git rev-parse --show-toplevel)/.codex/hooks/guardrails.py\" hook --format codex",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

- Codex sends the same fields as Claude Code (`tool_name`, `tool_input.command`, `cwd`) and accepts the same `hookSpecificOutput.permissionDecision: "deny"` answer.
- Codex does not support `"ask"` in PreToolUse, so `--format codex` turns every ask rule into a deny whose reason says the user must approve it. Disable rules you'd rather allow (`--disable commit-protected`).
- Codex asks the user to review and trust a new non-managed hook before it runs (`/hooks` in the CLI). Project hooks load only when the project's `.codex/` layer is trusted.
- The Codex docs path uses `git rev-parse --show-toplevel` because Codex may start in a subdirectory.

## Everything else

For agents without a command hook you can verify (Cline, Windsurf, Copilot, Aider, Gemini CLI and others), paste the plain rule list into their instructions file:

```bash
python3 scripts/guardrails.py rules --text >> AGENTS.md      # or .clinerules, .windsurfrules, etc.
```

This is guidance the model reads, not enforcement. Pair it with the agent's own approval settings for terminal commands.
