# Command discovery and utility tools

All three tools are opt-in through the existing configuration:

```text
[p]agent tool enable commands
[p]agent tool enable birthday
[p]agent tool enable reminder
```

`[p]` is your bot prefix. No additional prompt key or API key is required. Any
instructions about when to use these tools belong in your existing editable
responder prompt.

## Commands

`commands({"query":"birthday set"})` returns usage and bounded help from the
currently registered Red commands. A search term returns short matching entries.
Aliases and the server's prefixes are supported. Hidden commands, disabled cogs
and commands, and commands the requester cannot use are filtered out, including
parent-command permissions. The tool does not execute commands and rejects extra
arguments such as `run`.

Output settings use the existing global tool settings interface:

```text
[p]agent tool setting command_search_results 5
[p]agent tool setting command_help_chars 1200
```

The defaults are five matches and 1,200 output characters. Limits are clamped to
1–20 matches and 1–4,000 characters. No command catalog is added to the prompt.
Discovery covers prefix commands, including hybrid commands; slash-only commands
are not included.

## Birthdays

Requires the loaded `Birthday` cog from [Vex-Cogs](https://github.com/Vexed01/Vex-Cogs)
and its existing server setup (`[p]bdset interactive`).

```text
set_birthday({"date":"1999-07-31"})
set_birthday({"date":"07-31"})
```

The tool writes the requesting member's birthday in the current server using the
Birthday cog's Config. The year is optional; invalid dates, future birthdays and
years below the installed cog's minimum are rejected. Existing birthdays are
replaced, as with `birthday set`. It does not expose other members' birthdays or
accept a target user ID.

## Reminders

Requires the loaded `RemindMe` cog from [PCXCogs](https://github.com/PhasecoreX/PCXCogs).

```text
set_reminder({"seconds":7200,"text":"Check the laundry"})
```

Creates a one-time reminder for the requester, with a delay of at least 60 seconds.
Text is checked against the installed cog's length limit. `insert_reminder()`
handles the user limit, persistence and scheduling. The original message link is
retained. Delivery uses the cog's existing DM behavior, not the channel. Repeating
reminders, absolute local times, listing and deletion remain available through the
existing Discord commands.

## Execution and cost

- Both write tools require a message addressing the bot and are excluded from
  automatic responses. The model chooses whether a directly addressed request
  calls for a write; there is no separate natural-language intent classifier.
- User/guild restrictions, command checks (including parents) and disabled-command
  state are checked before writing. No command callbacks are invoked.
- Schemas are omitted if the required cog is not loaded. The tools remain disabled
  until explicitly enabled with the commands above.
- Identical write arguments are deduplicated within one response. This is not
  persistent deduplication across restarts or a newly processed message.
- The three schemas together measure approximately 204 tokens with the agent's
  `o200k_base`/`json.dumps` counter. Provider billing can differ. Tool results are
  short strings, and schema ordering is stable.

## Verification

In an environment with Red, discord.py and the Agent cog's dependencies:

```sh
python -m unittest discover -s tests -p test_agent_utilities.py -v
mypy --strict --follow-imports=silent agent/utility_cogs.py agent/tools/base.py agent/tools/commands.py agent/tools/birthday.py agent/tools/reminders.py
```

Tests use actual Red command/permission objects with mocked Discord transport and
utility storage. They do not connect to Discord or modify live configuration.
