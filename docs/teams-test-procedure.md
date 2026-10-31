# Teams test: agent as an independent BA

Goal: you (as the client, on your phone) explain a school portal in plain words. The laptop is the agent: it listens to the Teams audio, speaks its questions into the meeting, then writes an Epic with linked Stories into the SCRUM backlog without human approval.

## One-time setup (laptop)
1. Teams desktop app installed and signed in (a free account is fine). You create the meeting link; the laptop joins as a participant named **"BA assistant (AI, transcribing)"** (this is the visible disclosure; the agent also states it aloud at the start).
2. Install **VB-Audio Virtual Cable** (free driver, needs an admin prompt, sometimes a reboot). It creates two devices: *CABLE Input* (the agent plays its voice here) and *CABLE Output* (Teams uses it as the microphone).
3. In Teams on the laptop: Settings > Devices > **Microphone = CABLE Output**; **Speaker = the same device Windows uses by default** (the capture records that device's audio, i.e. your phone's voice). Do not select the laptop's physical microphone.
4. Use **headphones on the phone** (or sit in another room) so the phone's microphone does not hear the laptop speaker.
5. Confirm devices: `ba capture --list-devices` and `ba capture --list-voices`.

## Dashboard route (recommended)
Open the dashboard > **Live capture**, fill the form (client, project, title, Interview, autopilot, the other person's name, consent), use the two test buttons, join the Teams call, then press **Start capture**. End with **End call & analyze**. The terminal procedure below does the same thing.

## Start (three terminals, in `D:\BaAi\backend`)
```powershell
# 1. API
python -m uv run uvicorn --factory app.api.main:create_app --host 127.0.0.1 --port 8000

# 2. Worker: LLM + embeddings + AUTONOMOUS (no human approval; SCRUM only, kill switch available)
$env:LLM_PROVIDER="claude_cli"; $env:EMBEDDING_PROVIDER="fastembed"; $env:AUTONOMY_MODE="autonomous"
python -m uv run ba worker

# 3. Capture + interviewer (after you have joined the Teams meeting from the laptop)
$env:BA_EMAIL="operator@example.com"; $env:BA_PASSWORD="<your password>"
python -m uv run ba capture <client_id> "School portal kickoff" `
  --consent-basis "Operator (Zain) is the only participant and consents" `
  --interview --autopilot --no-mic `
  --remote-name "Zain" --remote-role "Client sponsor" --remote-authority `
  --project-id <project_id> --agent-output-device "CABLE Input" --agent-voice "Microsoft Zira Desktop"
```
Create the client and project once: `ba create-client "School project"`, `ba create-project <client_id> "School Portal" --jira-key SCRUM`.

## What to expect
- The agent introduces itself aloud, then asks one plain question at a time, with examples. Talk like a layperson; it confirms back ("To confirm: ... Is that right?"); say "yes" or correct it.
- It closes by itself after about 13 topics, then ends the meeting. Analysis and Jira creation run in the worker window, usually within a few minutes.
- Result: one **Epic** (scope, constraints and success measures from you) and **Stories linked under it**, all in the backlog. Requirements that are still too vague to test are **held** and listed in the autopilot report instead of being pushed.

## Check the result
```powershell
python -m uv run ba minutes <client_id> <meeting_id>        # draft minutes with evidence
# autopilot report (what was created, what was held and why): GET /clients/<id>/meetings/<mid>/autopilot
python -m uv run ba jira intents <client_id>
```
Find the issues in Jira with JQL: `labels = ba-generated ORDER BY created DESC`.

## Safety net (kept even with no approvals)
Kill switch (`ba kill-switch on` or the dashboard button) stops all Jira writes immediately; only the SCRUM project is allowed; every action is audited; duplicates are prevented by the `ba-intent-` label; the agent states that it is an AI and that no audio is saved.

## If something goes wrong
- Agent silent: check the capture window for `[status]` lines (API unreachable, voice failed, audio source failed) - they are never hidden.
- Agent talks over you: tell me; barge-in is on for the virtual-cable route.
- Echo or the agent hearing itself: the phone speaker is near the laptop mic, or Teams speaker is not the device being captured.
