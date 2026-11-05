# Roadmap: Natural Language Command Triggering

## Overview
Enable the LLM to recognize natural language commands (e.g., "commit", "push", "show status") and automatically trigger the corresponding slash commands (`/commit`, `/push`, `/status`) without requiring the user to type the exact slash command syntax.

## Current State
- Commands are triggered only by exact string matches: `/commit`, `/push`, `/status`, `/files`, `/diff`, `/reset`, `/ask`, `/read-only`, `/code`, `/todo`, `/commit-push`, `/project`, `/exit`
- The LLM has no awareness of these commands in the system prompt
- Users must memorize and type exact slash command syntax

## Target State
- LLM recognizes natural language intent and suggests/executes appropriate commands
- Example: User says "commit my changes" → LLM triggers `/commit`
- Example: User says "push to remote" → LLM triggers `/push`
- Example: User says "what's my git status" → LLM triggers `/status`
- Seamless fallback: if LLM doesn't recognize, user can still use slash commands

---

## Phase 1: System Prompt Enhancement (Week 1)

### 1.1 Add Command Registry to System Prompt
- Document all available commands with:
  - Command name (e.g., `/commit`)
  - Natural language triggers (e.g., "commit", "commit changes", "save changes")
  - Description of what it does
  - Parameters/options (e.g., `--message-only`)

### 1.2 Define Command Schema
```json
{
  "commands": [
    {
      "slash_command": "/commit",
      "triggers": ["commit", "commit changes", "save changes", "git commit"],
      "description": "Commit staged changes with generated message",
      "params": ["--message-only"]
    },
    {
      "slash_command": "/push",
      "triggers": ["push", "push to remote", "git push"],
      "description": "Push commits to remote"
    },
    ...
  ]
}
```

### 1.3 Update SYSTEM_PROMPT
- Include command registry in system prompt
- Add instruction: "When user expresses intent matching a command trigger, respond with the slash command to execute it"

---

## Phase 2: LLM-Driven Command Detection (Week 2)

### 2.1 Add Pre-Processing Hook
- Before sending user message to LLM, check for obvious command triggers
- Simple keyword matching for high-confidence triggers
- If match found, prepend instruction to user message: "The user wants to [action]. Execute the appropriate slash command."

### 2.2 LLM Tool for Command Execution
- Add a tool `execute_slash_command(command: str)` that the LLM can call
- Tool validates command exists and executes it
- Returns command output to LLM for response formulation

### 2.3 Structured Output Format
- LLM responds with either:
  - Normal chat response
  - Tool call to `execute_slash_command`
- Parse and execute tool calls in the agent loop

---

## Phase 3: Context-Aware Command Suggestions (Week 3)

### 3.1 Git Status Awareness
- Include `git status --short` output in context when relevant
- LLM can suggest `/commit` only when there are changes to commit
- LLM can suggest `/push` only when there are unpushed commits

### 3.2 Project State Awareness
- Track current mode (ask/code/read-only)
- Suggest `/code` when user asks for code changes in ask mode
- Suggest `/ask` when user wants chat-only in code mode

### 3.3 Conversation History Analysis
- Detect patterns: user frequently commits after "done" or "finished"
- Learn user's vocabulary for commands

---

## Phase 4: Advanced Features (Week 4+)

### 4.1 Command Chaining
- Support "commit and push" → executes `/commit-push`
- Support "stage all and commit" → runs `git add -A` then `/commit`

### 4.2 Parameter Inference
- "commit with message 'fix bug'" → `/commit` with custom message
- "push to origin main" → `/push` with remote/branch (if supported)

### 4.3 Confirmation Flow
- For destructive commands, LLM asks for confirmation before executing
- Configurable auto-approval per command category

### 4.4 Feedback Loop
- Log command triggers (natural language → slash command)
- Analyze false positives/negatives
- Improve trigger phrases over time

---

## Technical Implementation Details

### Files to Modify
1. `agent.py` - Main changes:
   - `SYSTEM_PROMPT` - Add command registry
   - `main()` loop - Add pre-processing hook
   - Add `execute_slash_command` tool definition
   - Handle tool calls in agent loop

2. `shai_config.json` - Add configuration:
   ```json
   {
     "natural_language_commands": {
       "enabled": true,
       "auto_execute": false,
       "confirm_destructive": true
     }
   }
   ```

### Tool Definition Example
```python
{
    "name": "execute_slash_command",
    "description": "Execute a slash command. Use when user expresses intent matching a known command.",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The slash command to execute (e.g., '/commit', '/push', '/status')"
            }
        },
        "required": ["command"]
    }
}
```

### Command Registry (in agent.py)
```python
COMMAND_REGISTRY = [
    {
        "slash_command": "/commit",
        "triggers": ["commit", "commit changes", "save changes", "git commit"],
        "description": "Commit staged changes with generated message. Use --message-only to only generate message.",
        "category": "git",
        "destructive": False
    },
    {
        "slash_command": "/push",
        "triggers": ["push", "push to remote", "git push"],
        "description": "Push commits to remote",
        "category": "git",
        "destructive": False
    },
    ...
]
```

---

## Success Metrics
- **Trigger Accuracy**: % of natural language commands correctly mapped to slash commands
- **False Positive Rate**: % of non-command messages incorrectly triggering commands
- **User Adoption**: % of commands executed via natural language vs slash commands
- **Latency**: Added response time for command detection

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| LLM hallucinates commands | Validate command exists in registry before execution |
| Accidental destructive actions | Require confirmation for destructive commands; config to disable auto-execute |
| Increased token usage | Keep command registry concise; only include in system prompt when in code mode |
| Conflicts with chat | Clear distinction: only trigger on high-confidence intent matches |

---

## Rollout Plan
1. **Alpha**: Enable for internal testing, `auto_execute=false` (LLM suggests, user confirms)
2. **Beta**: Enable `auto_execute=true` for non-destructive commands
3. **GA**: Full rollout with all commands, configurable per user

---

## Future Enhancements
- Voice command support
- Custom user-defined command aliases
- Command macros (sequences of commands)
- Integration with IDE command palette