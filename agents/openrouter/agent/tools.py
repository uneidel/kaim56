# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""The tool table: BUILTIN (name -> function, schema), the per-instance allowlist, the schema handed to the LLM, tool execution with audit, the hard denylist and the optional HITL approval, plugins, and init().

Part of the openrouter agent package (runs inside the VM): no import from the package root. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import time

from . import config as _config
from . import learn as _learn
from . import mcp as _mcp
from . import mgrclient as _mgrclient
from . import observe as _observe
from . import offload as _offload
from . import tools_local as _tools_local
from . import tools_manager as _tools_manager


BUILTIN = {
    "bash": (_tools_local.t_bash, "Run a shell command in the workspace",
             {"command": {"type": "string", "description": "command"}}, ["command"]),
    "read_file": (_tools_local.t_read_file, "Read a file",
                  {"path": {"type": "string"}}, ["path"]),
    "write_file": (_tools_local.t_write_file, "Write a file",
                   {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    "write_xlsx": (_tools_local.t_write_xlsx, "Write a spreadsheet (.xlsx) into the workspace — for lists and tables the user "
                   "will filter or sort (jobs, results, inventories). rows: first row = header.",
                   {"path": {"type": "string", "description": "file name, e.g. jobs.xlsx"},
                    "rows": {"type": "array", "items": {}, "description": "list of rows (arrays; first = header) or of objects (keys = header)"},
                    "sheet": {"type": "string", "description": "sheet name (optional)"}}, ["path", "rows"]),
    "write_docx": (_tools_local.t_write_docx, "Write a Word document (.docx) into the workspace from Markdown "
                   "(# headings, - bullets, paragraphs, **bold**) — for letters, reports, CVs.",
                   {"path": {"type": "string", "description": "file name, e.g. anschreiben.docx"},
                    "markdown": {"type": "string"},
                    "title": {"type": "string", "description": "document title (optional)"}}, ["path", "markdown"]),
    "list_dir": (_tools_local.t_list_dir, "List a directory",
                 {"path": {"type": "string"}}, []),
    "http_fetch": (_tools_local.t_http_fetch,
                   "Fetch a URL. HTML comes back as readable TEXT with link targets "
                   "in brackets — follow them with another fetch. raw=true for the "
                   "unconverted body.",
                   {"url": {"type": "string"},
                    "method": {"type": "string", "description": "GET (default) or POST"},
                    "raw": {"type": "boolean", "description": "true = raw HTML/body"}},
                   ["url"]),
    "read_pdf": (_tools_local.t_read_pdf, "Extract text from a PDF — path is a workspace file OR an http(s) URL; pages optional as a range (e.g. '1-5').",
                 {"path": {"type": "string", "description": "file in the workspace or http(s) URL"},
                  "pages": {"type": "string", "description": "optional page range, e.g. '1-5'"}}, ["path"]),
    "web_search": (_tools_local.t_web_search,
                   "Web search (Brave Search API via the manager; DuckDuckGo/Bing "
                   "as fallback). Returns title + URL + snippet.",
                   {"query": {"type": "string", "description": "search terms"},
                    "count": {"type": "integer", "description": "results (1-10, default 5)"}},
                   ["query"]),
    "spawn_subagent": (_tools_manager.t_spawn_subagent,
                       "Delegate a self-contained subtask to a fresh ephemeral VM and wait for its answer "
                       "(the manager creates and deletes the VM). Optionally pick the subagent's model — "
                       "e.g. a cheap/fast one for grunt work or a strong one for hard reasoning.",
                       {"task": {"type": "string", "description": "task for the subagent (self-contained: it has no memory of this chat)"},
                        "model": {"type": "string", "description": "optional OpenRouter model id for the subagent, e.g. google/gemini-2.5-flash"},
                        "tools": {"type": "string", "description": "optional: comma-separated subset of your own tools the subagent may use (narrower cage), e.g. 'bash,read_file,write_file'"},
                        "egress": {"type": "string", "description": "optional: comma-separated hosts the subagent may reach, or 'none' for no network at all"},
                        "skill": {"type": "string", "description": "optional: a skill from list_skills baked into the subagent's system prompt; without `tools` it then gets only the file/web tools"},
                        "persona": {"type": "string", "description": "optional: a named agent persona (e.g. code-reviewer, security-reviewer) baked into the subagent's system prompt; its recommended tools/model apply unless you override them"}}, ["task"]),
    "create_task": (_tools_manager.t_create_task,
                    "Queue a task — IMPORTANT: choose target by capability. "
                    "If the task needs a specific MCP/token (e.g. Home Assistant), "
                    "use the matching instance as target (e.g. 'hass'). For general/"
                    "isolated work use 'ephemeral' (fresh VM, deleted afterwards). schedule "
                    "optional ('every 2h','daily 08:00','hourly'). wait=true waits for the "
                    "result, otherwise it runs in the background and appears in the chat.",
                    {"task": {"type": "string", "description": "what should be done"},
                     "target": {"type": "string", "description": "instance name (capable) or 'ephemeral'"},
                     "schedule": {"type": "string", "description": "optional: every Nm|Nh|Nd, daily HH:MM, hourly"},
                     "wait": {"type": "boolean", "description": "wait for the result (default false)"},
                     "model": {"type": "string", "description": "optional OpenRouter model for an ephemeral target"}}, ["task"]),
    "mission_start": (_tools_manager.t_mission_start,
                      "Create a multi-stage assignment as a mission (goal + steps). For anything "
                      "that needs several tasks/days — the progress survives restarts.",
                      {"goal": {"type": "string", "description": "goal of the mission"},
                       "steps": {"type": "array", "items": {"type": "string"},
                                 "description": "planned steps in order"}},
                      ["goal", "steps"]),
    "missions": (_tools_manager.t_missions, "List open missions with steps/status.", {}, []),
    "mission_update": (_tools_manager.t_mission_update,
                       "Advance a mission step: set status (doing/done/failed), "
                       "record result + task_id AND the target instance of the kicked-off "
                       "task, add_step appends a step.",
                       {"id": {"type": "string", "description": "mission ID"},
                        "step": {"type": "integer", "description": "step number"},
                        "status": {"type": "string", "description": "open|doing|done|failed"},
                        "result": {"type": "string", "description": "short result"},
                        "task_id": {"type": "string", "description": "ID of the create_task task"},
                        "add_step": {"type": "string", "description": "append a new step"},
                        "note": {"type": "string", "description": "log note only"},
                        "target": {"type": "string",
                                   "description": "instance the step was delegated to "
                                                  "(create_task target)"}}, ["id"]),
    "mission_finish": (_tools_manager.t_mission_finish,
                       "Finish a mission; failed=true on failure. Provide a short conclusion.",
                       {"id": {"type": "string"}, "summary": {"type": "string"},
                        "failed": {"type": "boolean"}}, ["id", "summary"]),
    "oracle": (_tools_manager.t_oracle,
               "Second opinion BEFORE a risky/irreversible action: challenges your "
               "assumptions, never acts itself. plan = what you intend and why; kontext = "
               "relevant facts (IDs, wordings, user assignment). On 'OBJECTION' do not "
               "act, but resolve it or ask back.",
               {"plan": {"type": "string", "description": "planned action + reasoning"},
                "kontext": {"type": "string", "description": "facts: IDs, wordings, assignment"}},
               ["plan"]),
    "ha_control": (_tools_manager.t_ha_control,
                   "Turn a Home Assistant device OR whole room on/off by the SPOKEN name "
                   "(manager matches real entities/areas server-side and auto-learns the "
                   "alias on a fuzzy hit). Prefer this over raw HA intents for voice control: "
                   "pass the heard target verbatim and action on/off. Handles rooms too "
                   "('Licht im Gartenhaus').",
                   {"spoken": {"type": "string", "description": "the spoken target, e.g. 'Gartenhaus denke rechts' or 'Licht im Gartenhaus'"},
                    "action": {"type": "string", "description": "'on' or 'off'"}},
                   ["spoken", "action"]),
    "ha_learn_alias": (_tools_manager.t_ha_learn_alias,
                       "Teach Home Assistant a spoken-name alias for an entity so the same "
                       "misheard wording matches natively next time (STT hears 'Decke' as "
                       "'denke'). Call it after recovering from a failed HA intent, with the "
                       "words you originally heard and the real entity id.",
                       {"spoken": {"type": "string", "description": "the spoken/misheard name, e.g. 'Gartenhaus denke rechts'"},
                        "entity": {"type": "string", "description": "real entity id, e.g. 'light.gartenhaus_decke_rechts'"}},
                       ["spoken", "entity"]),
    "notify": (_tools_manager.t_notify,
               "Push notification to the user's devices (app system notification + "
               "web-manager bell). For important events/results when they are not in the "
               "chat. Unlike send_signal this is the app/web channel, does not ring "
               "in Signal.",
               {"title": {"type": "string", "description": "short title"},
                "message": {"type": "string", "description": "text of the notification"}},
               ["title"]),
    "send_signal": (_tools_manager.t_send_signal,
                    "Send the user a Signal message — for results, findings "
                    "or questions when they are not currently in the chat. Do NOT use for the "
                    "normal reply in an ongoing conversation (that arrives anyway) "
                    "and not repeatedly unprompted: a message rings on a "
                    "phone. Recipients only from the allowed list; leaving 'to' empty "
                    "means: to the default recipient.",
                    {"text": {"type": "string", "description": "message text"},
                     "to": {"type": "string", "description": "optional: number in the format +49…"}},
                    ["text"]),
    "send_mail": (_tools_manager.t_send_mail,
                  "Send an e-mail from your own address (the manager's mailbox, "
                  "<account>+<your name>@<domain>). For results or questions that belong "
                  "in mail, or to answer a mail you were asked about. Recipients only from "
                  "the allowed list; plain text, no Markdown. Do NOT use for the normal "
                  "reply to a mail turn — that reply is mailed back automatically.",
                  {"to": {"type": "string", "description": "recipient address"},
                   "subject": {"type": "string", "description": "subject line"},
                   "text": {"type": "string", "description": "plain-text body"}},
                  ["to", "subject", "text"]),
    "read_inbox": (_tools_manager.t_read_inbox,
                  "Read new user messages (Signal/app/web) since the last run — "
                  "the orchestrator's inbox. Each message comes only once (watermark); "
                  "peek=true to preview without consuming.",
                  {"peek": {"type": "boolean", "description": "only look, do not consume"}}, []),
    "list_agents": (_tools_manager.t_list_agents,
                    "List available agent instances + capabilities (model/MCP). "
                    "For routing: choose the create_task target by capability.",
                    {}, []),
    "recall_tasks": (_tools_manager.t_recall_tasks,
                     "Query previously executed tasks + results (long-term memory). "
                     "Without query the most recent, with query search specifically. Use BEFORE create_task "
                     "to check whether something is already done/scheduled (no duplicates).",
                     {"query": {"type": "string", "description": "search term (empty = most recent)"},
                      "limit": {"type": "integer", "description": "max hits (default 10)"}}, []),
    "list_tasks": (_tools_manager.t_list_tasks,
                   "List RUNNING/scheduled tasks with IDs — for targeted deletion. "
                   "(recall_tasks, by contrast, is the history of completed runs.)", {}, []),
    "delete_task": (_tools_manager.t_delete_task,
                    "Delete a running/scheduled task by ID. Get the ID first with "
                    "list_tasks. Final.",
                    {"id": {"type": "string", "description": "task ID from list_tasks"}}, ["id"]),
    "edit_task": (_tools_manager.t_edit_task,
                  "Change the message and/or schedule of a task (ID from list_tasks). "
                  "schedule e.g. 'every 2h', 'daily 08:00', 'hourly'; empty = one-off.",
                  {"id": {"type": "string", "description": "task ID from list_tasks"},
                   "message": {"type": "string", "description": "new text (empty = unchanged)"},
                   "schedule": {"type": "string", "description": "new schedule (empty = one-off/unchanged)"}},
                  ["id"]),
    "search_sessions": (_tools_manager.t_search_sessions,
                        "Full-text search over earlier chats and task results (exact words, "
                        "newest and best matches first). Use memory_recall for meaning, "
                        "this for names, numbers, URLs you remember seeing.",
                        {"query": {"type": "string", "description": "words to look for"},
                         "instance": {"type": "string", "description": "optional: another instance (orchestrator only)"}},
                        ["query"]),
    "propose_skill": (_tools_manager.t_propose_skill,
                      "Propose a reusable procedure as a skill for the catalog (after a "
                      "non-trivial task that worked, or after the user corrected your approach). "
                      "The operator approves it in the Skills tab.",
                      {"name": {"type": "string", "description": "kebab-case name"},
                       "description": {"type": "string", "description": "one line: what it is for"},
                       "content": {"type": "string", "description": "Markdown: purpose, when to use, exact steps and tools, pitfalls; no secrets"}},
                      ["name", "description", "content"]),
    "list_skills": (_tools_manager.t_list_skills,
                    "List available expert skills. Without arguments: names only. "
                    "query='…' searches names AND descriptions. Before specialized "
                    "tasks, check whether a matching skill exists.",
                    {"query": {"type": "string",
                               "description": "optional: filter, e.g. 'docker'"},
                     "category": {"type": "string",
                                  "description": "optional: coding, design, devops, data, research, writing, meta, "
                                                 "personal or security (security only when enabled for you)"}},
                    []),

    "load_skill": (_tools_manager.t_load_skill, "Load an expert skill (knowledge document) into the context and follow it.",
                   {"name": {"type": "string", "description": "skill name from list_skills"}}, ["name"]),
    "memory_store": (_tools_manager.t_memory_store, "Store a value permanently (survives restart/instance deletion).",
                     {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
    "memory_recall": (_tools_manager.t_memory_recall, "Retrieve a stored value; without key all entries.",
                      {"key": {"type": "string"}}, []),
    "memory_reflect": (_tools_manager.t_memory_reflect,
                       "Ask the long-term memory a question and get a reasoned answer over everything "
                       "remembered (past conversations, notes). Use for 'what do we know about…', "
                       "'what did the user say about…', preferences and history.",
                       {"question": {"type": "string"}}, ["question"]),
    "playbook_add": (_tools_manager.t_playbook_add,
                     "Record a permanent rule/procedure — applies ALWAYS from now on. "
                     "Use this when the user tells you HOW something is to be done, states a "
                     "lasting preference or corrects you.",
                     {"rule": {"type": "string", "description": "the rule as a short, concrete sentence"}}, ["rule"]),
    "playbooks": (_tools_manager.t_playbooks, "Show all fixed rules (playbooks) with IDs.", {}, []),
    "playbook_forget": (_tools_manager.t_playbook_forget, "Remove a rule by ID (ID from playbooks).",
                        {"id": {"type": "string", "description": "playbook ID"}}, ["id"]),
    "remote_ls": (_tools_manager.t_remote_ls,
                  "List the folder the user has shared (lives on THEIR machine, "
                  "connected via P2P). Paths are relative to the root of the share.",
                  {"path": {"type": "string", "description": "relative, default '.'"}}, []),
    "remote_read": (_tools_manager.t_remote_read,
                    "Read a file from the user's shared folder (path relative to the share).",
                    {"path": {"type": "string"}}, ["path"]),
    "remote_write": (_tools_manager.t_remote_write,
                     "Write a file to the user's shared folder — CREATES and "
                     "OVERWRITES, missing subfolders are created automatically. Write access "
                     "is explicitly allowed: when the user wants to put, save or "
                     "change something there, CALL THIS TOOL instead of claiming you cannot "
                     "write. Only if it returns an error is it not possible.",
                     {"path": {"type": "string", "description": "relative to the share, e.g. 'note.txt'"},
                      "content": {"type": "string", "description": "complete new file content"}},
                     ["path", "content"]),
    "remote_delete": (_tools_manager.t_remote_delete,
                      "Delete a file or folder in the user's shared folder. "
                      "Irreversible — there is no trash. Only delete when the user "
                      "requests it, and ask first when in doubt. A non-empty folder "
                      "fails on purpose; set recursive=true for that.",
                      {"path": {"type": "string", "description": "relative to the share"},
                       "recursive": {"type": "boolean",
                                     "description": "delete the folder including its contents (default false)"}},
                      ["path"]),
    "project_status": (_tools_manager.t_project_status,
                       "As the LEAD of a project: every writer's changes (files, commits ahead/behind "
                       "the base branch). Their folders are readable at /project/<name>.members/<writer>.",
                       {}, []),
    "project_diff": (_tools_manager.t_project_diff,
                     "As the LEAD of a project: one writer's changes as a patch — review before merging.",
                     {"project": {"type": "string"}, "member": {"type": "string", "description": "the writer instance"}},
                     ["project", "member"]),
    "project_merge": (_tools_manager.t_project_merge,
                      "As the LEAD of a project: merge one writer's work into the base branch. Only where the "
                      "operator allowed it; a conflict changes nothing and names the files.",
                      {"project": {"type": "string"}, "member": {"type": "string", "description": "the writer instance"}},
                      ["project", "member"]),
    "squad_post": (_tools_manager.t_squad_post,
                   "Post to your project's squad channel. Members you address (to, or @name / @all in the "
                   "text) are woken to act on it. Reply to a message with reply_to=<its id>. Address only "
                   "those who need to act; the operator reads the channel.",
                   {"to": {"type": "string", "description": "member name(s), comma-separated, or 'all'; 'user' or empty = the operator (the person who wrote last); empty with reply_to = that message's sender"},
                    "text": {"type": "string"},
                    "reply_to": {"type": "string", "description": "optional: id of the message you answer"},
                    "project": {"type": "string", "description": "optional when you are in one squad"}},
                   ["text"]),
    "squad_ask": (_tools_manager.t_squad_ask,
                  "Ask ONE squad member a question; it is woken and answers with squad_post reply_to. "
                  "wait=true (default) blocks until the answer or the timeout.",
                  {"to": {"type": "string", "description": "one member name"},
                   "question": {"type": "string"},
                   "wait": {"type": "boolean", "description": "default true"},
                   "timeout": {"type": "integer", "description": "seconds, default 600"},
                   "project": {"type": "string", "description": "optional when you are in one squad"}},
                  ["to", "question"]),
    "squad_read": (_tools_manager.t_squad_read,
                   "Read your squad channel: messages to you or to all, and your own (oldest first).",
                   {"since": {"type": "string", "description": "optional: only messages after this id"},
                    "project": {"type": "string", "description": "optional when you are in one squad"}},
                   []),
    "squad_members": (_tools_manager.t_squad_members, "The members of your squad (project) and their roles.",
                      {"project": {"type": "string", "description": "optional when you are in one squad"}}, []),
    "squad_catalog": (_tools_manager.t_squad_catalog,
                      "Lead: what squad members can be made of — personas, skills (standard ones flagged), "
                      "model tiers with their models, templates (openrouter, claude). Call it before squad_propose.",
                      {"project": {"type": "string", "description": "optional when you lead one project"}}, []),
    "squad_propose": (_tools_manager.t_squad_propose,
                      "Lead only: propose the squad plan to the user — goal, members (new or existing instances) "
                      "and tickets. Pick each member's persona, skills, model_tier and template from squad_catalog; "
                      "when no persona fits, give new_persona (created only if the user approves). NOTHING is "
                      "created or started until the user approves it in the UI; the decision arrives as a squad "
                      "message. One pending plan per project. The user may ask for changes instead (a squad "
                      "message 'Revise proposal <id>'): then call squad_propose again with the whole updated plan "
                      "— it replaces that proposal as its next version.",
                      {"goal": {"type": "string"},
                       "members": {"type": "array", "description": "the team", "items": {"type": "object", "properties": {
                           "name": {"type": "string", "description": "a-z 0-9 -"},
                           "purpose": {"type": "string"},
                           "role": {"type": "string", "enum": ["writer", "reader", "lead"]},
                           "existing": {"type": "boolean", "description": "an instance that exists already"},
                           "persona": {"type": "string", "description": "a persona name from squad_catalog"},
                           "new_persona": {"type": "object", "description": "only when no persona fits; created on approval",
                                           "properties": {"name": {"type": "string", "description": "a-z 0-9 -, not existing"},
                                                          "description": {"type": "string", "description": "one line"},
                                                          "prompt": {"type": "string", "description": "the system prompt"},
                                                          "tools": {"type": "array", "items": {"type": "string"}}}},
                           "skills": {"type": "array", "items": {"type": "string"}},
                           "model_tier": {"type": "string", "enum": ["cheap", "strong", "code", "local"]},
                           "model": {"type": "string", "description": "optional concrete model"},
                           "template": {"type": "string", "enum": ["openrouter", "claude"],
                                        "description": "openrouter (API model, counts against the token budget, default) "
                                                       "or claude (Claude Code on the user's subscription)"}}}},
                       "tickets": {"type": "array", "items": {"type": "object", "properties": {
                           "key": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
                           "assignee": {"type": "string", "description": "a member name"},
                           "depends_on": {"type": "array", "items": {"type": "string"}, "description": "ticket keys"}}}},
                       "budget_tokens_per_day": {"type": "integer"},
                       "project": {"type": "string", "description": "optional when you lead one project"}},
                      ["goal", "members"]),
    "squad_request_member": (_tools_manager.t_squad_request_member,
                             "Lead only, after the plan is approved: ask the user for one more member "
                             "(same fields as a squad_propose member). Nothing happens before approval.",
                             {"member": {"type": "object"}, "reason": {"type": "string"},
                              "project": {"type": "string", "description": "optional when you lead one project"}},
                             ["member"]),
    "squad_request_merge": (_tools_manager.t_squad_request_merge,
                            "Lead only: ask the user to merge a writer's branch to the project's base. "
                            "The user reviews the diff and approves.",
                            {"writer": {"type": "string"}, "summary": {"type": "string"},
                             "project": {"type": "string", "description": "optional when you lead one project"}},
                            ["writer", "summary"]),
    "ticket_list": (_tools_manager.t_ticket_list, "The squad's tickets (id, key, status, assignee, dependencies).",
                    {"project": {"type": "string", "description": "optional when you are in one squad"}}, []),
    "ticket_update": (_tools_manager.t_ticket_update,
                      "Your ticket (or any, as the lead): set status open/doing/review/done/blocked and/or add a "
                      "comment. Done wakes the members whose tickets waited on it.",
                      {"id": {"type": "string"}, "status": {"type": "string"}, "comment": {"type": "string"},
                       "project": {"type": "string", "description": "optional when you are in one squad"}},
                      ["id"]),
    "ticket_create": (_tools_manager.t_ticket_create,
                      "Lead only, after approval: a new ticket; with an assignee whose dependencies are done it "
                      "wakes the assignee.",
                      {"title": {"type": "string"}, "description": {"type": "string"},
                       "assignee": {"type": "string"}, "key": {"type": "string"},
                       "depends_on": {"type": "array", "items": {"type": "string"}, "description": "ticket keys"},
                       "project": {"type": "string", "description": "optional when you are in one squad"}},
                      ["title"]),
    "ticket_assign": (_tools_manager.t_ticket_assign,
                      "Lead only, after approval: assign a ticket; wakes the assignee when its dependencies are done.",
                      {"id": {"type": "string"}, "assignee": {"type": "string"},
                       "project": {"type": "string", "description": "optional when you are in one squad"}},
                      ["id", "assignee"]),
    "git_push_branch": (_tools_manager.t_git_push_branch,
                        "Squad git: snapshot your project folder and push its branch to squad/<you>/<name> on the "
                        "project's remote (runs on the host; you never hold the token). Writers; the lead names a writer.",
                        {"name": {"type": "string", "description": "branch name part, default the project name"},
                         "writer": {"type": "string", "description": "lead only: whose branch"},
                         "project": {"type": "string", "description": "optional when one of your projects has a git remote"}}, []),
    "git_fetch": (_tools_manager.t_git_fetch,
                  "Squad git: fetch the remote's branches into the project repo as refs/remotes/kaim56/*.",
                  {"project": {"type": "string", "description": "optional when one of your projects has a git remote"}}, []),
    "pr_create": (_tools_manager.t_pr_create,
                  "Squad git: push your branch and open a pull request to the base branch. Writers and the lead; "
                  "daily limit. Merging is left to the user.",
                  {"title": {"type": "string"}, "body": {"type": "string", "description": "markdown"},
                   "name": {"type": "string", "description": "branch name part, default the project name"},
                   "writer": {"type": "string", "description": "lead only: whose branch"},
                   "project": {"type": "string", "description": "optional when one of your projects has a git remote"}}, ["title"]),
    "pr_list": (_tools_manager.t_pr_list, "Squad git: the remote's pull requests.",
                {"state": {"type": "string", "enum": ["open", "closed", "all"]}, "project": {"type": "string", "description": "optional when one of your projects has a git remote"}}, []),
    "pr_view": (_tools_manager.t_pr_view, "Squad git: one pull request with its description and comments.",
                {"number": {"type": "integer"}, "project": {"type": "string", "description": "optional when one of your projects has a git remote"}}, ["number"]),
    "pr_comment": (_tools_manager.t_pr_comment, "Squad git: comment on a pull request.",
                   {"number": {"type": "integer"}, "body": {"type": "string"}, "project": {"type": "string", "description": "optional when one of your projects has a git remote"}}, ["number", "body"]),
    "issue_create": (_tools_manager.t_issue_create, "Squad git: open an issue on the project's remote (daily limit).",
                     {"title": {"type": "string"}, "body": {"type": "string"}, "project": {"type": "string", "description": "optional when one of your projects has a git remote"}}, ["title"]),
    "issue_list": (_tools_manager.t_issue_list, "Squad git: the remote's issues (without PRs).",
                   {"state": {"type": "string", "enum": ["open", "closed", "all"]}, "project": {"type": "string", "description": "optional when one of your projects has a git remote"}}, []),
    "issue_comment": (_tools_manager.t_issue_comment, "Squad git: comment on an issue.",
                      {"number": {"type": "integer"}, "body": {"type": "string"}, "project": {"type": "string", "description": "optional when one of your projects has a git remote"}}, ["number", "body"]),
    "ask_human": (_tools_manager.t_ask_human,
                  "Squad: ask the operator (a human) a question you cannot decide yourself — a product choice, "
                  "missing access, an unclear requirement. Your ticket is set blocked; END YOUR TURN after asking: "
                  "the answer wakes you as a new turn and sets the ticket back to doing.",
                  {"question": {"type": "string"},
                   "options": {"type": "array", "items": {"type": "string"}, "description": "optional: choices to pick from"},
                   "ticket": {"type": "string", "description": "optional: ticket key or id (default: your ticket in doing)"}},
                  ["question"]),
    "list_secrets": (_tools_manager.t_list_secrets, "Show the secret names released for this agent (no values).",
                     {}, []),
    "get_secret": (_tools_manager.t_get_secret, "Fetch a released secret (e.g. API key/token) only when needed. Never output values in replies/logs.",
                   {"name": {"type": "string"}}, ["name"]),
}


# Optional per-instance tool allowlist (AGENT_TOOLS, comma-separated). Empty =
# all. Filters both the schema reported to the model AND the
# execution — otherwise a model could call a disabled tool anyway.
# MCP tools are unaffected by this (those are controlled by MCP_SERVERS/policy).
_TOOL_ALLOW = {t.strip() for t in os.environ.get("AGENT_TOOLS", "").split(",") if t.strip()}


# Task administration only where the manager has set TASK_ADMIN (orchestrator).
# The MISSION tools are deliberately NOT in here: every agent may plan its own
# mission and delegate the steps to capable instances (create_task target). Each
# agent only ever sees and writes its own missions — the manager keys them by
# the calling instance.
_TASK_ADMIN_TOOLS = {"list_tasks", "delete_task", "edit_task"}


def tool_enabled(name):
    if name == "offload_read":
        return True   # system helper: must always be available, otherwise a reference dangles
    if name == "spawn_subagent" and os.environ.get("NO_SPAWN"):
        return False
    if name in _TASK_ADMIN_TOOLS and not os.environ.get("TASK_ADMIN"):
        return False
    return (not _TOOL_ALLOW) or name in _TOOL_ALLOW


def builtin_schema():
    out = []
    for name, (_fn, desc, props, req) in BUILTIN.items():
        if not tool_enabled(name):
            continue
        out.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}})
    return out


def _resolve_tool_name(name):
    """Models drop the MCP prefix now and then — 'mrmusic_power' for
    'mrmusic__mrmusic_power' (gemini-2.5-flash, 2026-09-07, 'unknown tool'
    twice while the user waited for the radio). A bare name that matches
    exactly ONE registered MCP tool is accepted; ambiguity stays unknown."""
    if name in BUILTIN or name in _mcp._mcp_tools:
        return name
    hits = [fq for fq, (_srv, tool) in _mcp._mcp_tools.items()
            if tool == name or fq.endswith("__" + name)]
    if len(hits) == 1:
        _config.log(f"tool name '{name}' resolved to '{hits[0]}'")
        return hits[0]
    return name


def _tools_report():
    """'/tools' — the registry as the model sees it, without a model call.
    Built-in tools that are enabled here, then every MCP tool with its full
    name. A smoke test after a rootfs rebuild reads exactly this."""
    builtin = sorted(n for n in BUILTIN if tool_enabled(n))
    mcp = sorted(_mcp._mcp_tools)
    out = [f"built-in ({len(builtin)}): " + ", ".join(builtin)]
    if mcp:
        out.append(f"mcp ({len(mcp)}): " + ", ".join(mcp))
    else:
        out.append("mcp (0): none registered")
    return "\n".join(out)


def exec_tool(name, args):
    name = _resolve_tool_name(name)
    t0 = time.monotonic()

    def _audit(**kw):        # every exit books the call with its duration
        _observe.audit(name, args, ms=int((time.monotonic() - t0) * 1000), **kw)
    # Hook/intervention: denylist + optional HITL approval BEFORE execution.
    allow, reason = _hook_before_tool(name, args)
    if not allow:
        _audit(ok=False)
        return f"Tool '{name}' not executed: {reason}"
    try:
        if name in BUILTIN:
            if not tool_enabled(name):
                _audit(ok=False, err="tool not enabled")
                return f"Tool '{name}' is not enabled for this instance."
            out = str(BUILTIN[name][0](**args))
        elif name in _mcp._mcp_tools:
            srv, tool = _mcp._mcp_tools[name]
            out = str(_mcp._mcp[srv].call(tool, args))
        else:
            _audit(ok=False, err="unknown tool")
            return f"unknown tool: {name}"
    except Exception as e:
        _audit(ok=False, err=repr(e))
        return f"Tool error ({name}): {e!r}"
    failed = _learn._looks_failed(out)
    _audit(ok=not failed, err=out[:300] if failed else "", result="" if failed else out[:200])
    return _offload._finalize_output(name, out)


BUILTIN["offload_read"] = (
    _offload.t_offload_read,
    "Re-read a previously offloaded, truncated tool output in chunks "
    "(the offload reference names id and offset).",
    {"id": {"type": "string", "description": "offload id from the reference"},
     "offset": {"type": "integer", "description": "start position (characters)"},
     "length": {"type": "integer", "description": "max characters (default 8000)"}},
    ["id"])



# --- 4b) tool hook: hard denylist + approvals --------------------------------
# Squad members (the manager's /api/hitl/policy names the squad): the squad's
# approval tools ("bash" = commands matching its pattern) plus HITL_TOOLS when
# HITL is on are asked for WITHOUT waiting — the call returns "waiting for
# approval <id>", the decision wakes the agent, and the same call then goes
# through once. Everyone else: the old opt-in HITL, polled for HITL_TIMEOUT.
HITL = os.environ.get("HITL", "") not in ("", "0", "false", "False")
HITL_TOOLS = set(t for t in os.environ.get(
    "HITL_TOOLS", "bash,remote_delete,remote_write,delete_task,edit_task").split(",") if t)
HITL_TIMEOUT = int(os.environ.get("HITL_TIMEOUT", "120"))
# Always active, independent of HITL: obviously destructive bash patterns.
_DENY_PATTERNS = ("rm -rf /", ":(){:|:&};:", "mkfs", "dd if=", "> /dev/sd", "chmod -R 000")
POLICY_TTL = 60
_policy = {"ts": 0.0, "v": {}}


def _hitl_policy():
    """The manager's approval policy for this instance (cached POLICY_TTL; {} when unreachable)."""
    if time.time() - _policy["ts"] < POLICY_TTL:
        return _policy["v"]
    try:
        v = json.loads(_mgrclient._mgr_get(_mgrclient._manager_base(), "/api/hitl/policy", timeout=6))
    except Exception:
        v = {}
    _policy.update(ts=time.time(), v=v if isinstance(v, dict) else {})
    return _policy["v"]


def _squad_gated(pol, name, args):
    if HITL and name in HITL_TOOLS:
        return True
    if name not in (pol.get("tools") or []):
        return False
    if name != "bash":
        return True
    try:
        return bool(re.search(pol.get("bash_re") or r"(?!)", str(args.get("command", ""))))
    except re.error:
        return True


def _hitl_target(name, args):
    return _observe._audit_target(name, args) or json.dumps(args or {}, sort_keys=True, ensure_ascii=False)[:200]


def _squad_approval(name, args):
    """(allow, reason) without waiting: approved once -> through; else the id to wait for."""
    try:
        d = json.loads(_mgrclient._mgr(_mgrclient._manager_base(), "/api/hitl",
                                       {"tool": name, "target": _hitl_target(name, args), "async": True}, timeout=8))
    except Exception:
        return True, ""
    if not d.get("id") or d.get("status") == "approved":
        return True, ""
    return False, f"waiting for approval {d['id']} — end your turn; you will be woken with the decision"


def _request_approval(name, args):
    """Request an approval from the manager (web, app, Signal) and poll for
    it. If the manager cannot (old version) -> do not block (True).
    Timeout/rejection -> False."""
    try:
        d = json.loads(_mgrclient._mgr(_mgrclient._manager_base(), "/api/hitl",
                            {"tool": name, "target": _observe._audit_target(name, args)}, timeout=8))
        hid = d.get("id")
        if not hid:
            return True
    except Exception:
        return True
    deadline = time.time() + HITL_TIMEOUT
    while time.time() < deadline:
        time.sleep(2)
        try:
            st = json.loads(_mgrclient._mgr_get(_mgrclient._manager_base(), f"/api/hitl/{hid}", timeout=6)).get("status")
        except Exception:
            continue
        if st == "approved":
            return True
        if st == "denied":
            return False
    return False


def _hook_before_tool(name, args):
    """(allow, reason). Denylist first, then the squad's approvals, else optional HITL."""
    if name == "bash":
        cmd = str(args.get("command", ""))
        for pat in _DENY_PATTERNS:
            if pat in cmd:
                return False, f"blocked by security rule ({pat})"
    pol = _hitl_policy()
    if pol.get("squad"):
        return _squad_approval(name, args) if _squad_gated(pol, name, args) else (True, "")
    if HITL and name in HITL_TOOLS:
        if not _request_approval(name, args):
            return False, "not approved by the user (or timed out)"
    return True, ""


TOOLS = []

# Tool plugins (pi.dev extension idea, ported): one .py file per tool,
# placed on the config disk by the manager (/config/plugins). Convention:
#   DESC = "…"; PARAMS = {...}; REQUIRED = [...];  def run(**kwargs): ...
# The filename (without .py) becomes the tool name. The microVM is the sandbox.
PLUGIN_TOOLS = set()
PLUGIN_DIR = os.environ.get("PLUGIN_DIR", "/config/plugins")


def load_plugins():
    import importlib.util, sys
    if not os.path.isdir(PLUGIN_DIR):
        return
    for entry in sorted(os.listdir(PLUGIN_DIR)):
        path = os.path.join(PLUGIN_DIR, entry)
        syspath_add = None
        if os.path.isdir(path):
            # multi-file tool: folder <name>/ with entry file tool.py (or
            # __init__.py / <name>.py). The folder goes on sys.path so that
            # internal imports (import helper) work.
            name = os.path.basename(path)
            src = None
            for cand in ("tool.py", "__init__.py", name + ".py"):
                if os.path.isfile(os.path.join(path, cand)):
                    src = os.path.join(path, cand); break
            if not src:
                _config.log(f"plugin '{name}' ignored: no tool.py/__init__.py in the folder")
                continue
            syspath_add = path
        elif path.endswith(".py"):
            name, src = os.path.basename(path)[:-3], path
        else:
            continue
        if name in BUILTIN and name not in PLUGIN_TOOLS:
            _config.log(f"plugin '{name}' ignored: collides with a built-in tool")
            continue
        try:
            if syspath_add and syspath_add not in sys.path:
                sys.path.insert(0, syspath_add)
            spec = importlib.util.spec_from_file_location("plugin_" + name, src)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            BUILTIN[name] = (mod.run, str(getattr(mod, "DESC", name))[:300],
                             getattr(mod, "PARAMS", {}), getattr(mod, "REQUIRED", []))
            PLUGIN_TOOLS.add(name)
            _config.log(f"plugin loaded: {name}")
        except Exception as e:
            _config.log(f"plugin '{name}' ERROR: {e!r}")


def init():
    global TOOLS
    os.makedirs(_config.WORKDIR, exist_ok=True)
    load_plugins()
    TOOLS = builtin_schema() + _mcp.init_mcp()
    _config.log(f"agent ready: backend={_config.LLM_BACKEND} url={_mgrclient._llm_url()} model={_config.OR_MODEL} tools={len(TOOLS)} workdir={_config.WORKDIR}")
