# 13 Labs demo release

The release branch packages the FastAPI backend and Vite app in one image. It starts one Uvicorn worker on the platform port and keeps SQLite and voice receipts in `/data`.

## Deploy

Push the tested `ops/demo-release` commit. On Maritime, deploy the branch with the repository’s Dockerfile. Set `COMMIT_SHA` to the full Git commit and check the build history against it.

```bash
maritime create thirteen-labs --repo https://github.com/rayanali2/13-Labs \
  --branch ops/demo-release --public --env STATE_DB=/data/13-labs.db \
  --env DEMO_MODE=live --env "COMMIT_SHA=$(git rev-parse HEAD)"
```

Use the public HTTPS address from Maritime as `PUBLIC_ORIGIN`. Store `ELEVENLABS_API_KEY` and `ELEVENLABS_WEBHOOK_SECRET` in the host’s encrypted environment settings. The release entrypoint also accepts the team’s existing `THIRTEEN_LABS_KEY` when `ELEVENLABS_API_KEY` is empty. Set `ELEVENLABS_LAB_AGENT_ID` and `ELEVENLABS_VOICE_AGENT_ID` to separate private agents. When needed, the text fallback uses `ANTHROPIC_API_KEY` and `LAB_MODEL` with the lab agent ID unset.

On Railway, deploy the same branch and Dockerfile and mount a persistent volume at `/data`. Set the same environment variables, using Railway’s HTTPS origin, and generate a public domain. Update the four voice webhook URLs to the actual host before making a call.

## Configure ElevenLabs

Use the four typed refund tools in `thirteen_labs.engine.tools`. The headless profile is text-only, allows the system prompt override, and waits for its client tools to return a result. The voice profile uses `REVISED_WORKFLOW` from `thirteen_labs.engine.evaluation`, speaks aloud, and disables browser prompt overrides.

For each voice tool in ElevenLabs, send `POST` to `/api/voice/tools/<tool>` at the public origin. Set `X-13-Labs-Webhook-Secret` from the ElevenLabs workspace secret store. Set `X-13-Labs-Voice-Session` from `secret__lab_voice_session` and `X-ElevenLabs-Conversation-Id` from `system__conversation_id`.

## Verify the deployment

```bash
REQUIRE_LIVE_PROVIDERS=1 scripts/smoke.sh "$PUBLIC_ORIGIN" "$COMMIT_SHA"
```

Open the app in a fresh browser. Start a shift, inspect the baseline, apply the revision, and open the report after the retest completes. On the Lab view, start a microphone call as the owner and ask about the cracked monitor in order `ORD-1302` with carrier reference `CARR-4702`. End the call and choose **Check call evidence**. Match the ElevenLabs conversation ID and the server receipts for the order lookup, carrier check, refund request, and ledger write. Use that evidence when reporting a successful microphone test.

Pass the report ID as `RUN_ID` to the smoke command, then repeat the check after restarting or redeploying. Keep the previous green commit for rollback.

## Preserve a measured run

```bash
uv run --frozen python scripts/export_replay.py export "$PUBLIC_ORIGIN" "$RUN_ID" replay.json
```

Upload the snapshot into `/data` and set `REPLAY_FILE` to its path. The container validates and imports it before startup. The replay gets a stable read-only ID, preserves the original provider receipts, and appears under **Open recorded run** without entering the live queue.

When the local Docker network needs a trusted development CA bundle, pass it as an optional BuildKit secret. The build defaults to the container’s trust store.

```bash
docker build --secret id=build_ca,src=/etc/ssl/certs/ca-certificates.crt -t thirteen-labs .
```
