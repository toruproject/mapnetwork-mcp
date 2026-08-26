# Building a local-test `.mcpb`

For testing changes against a local backend (e.g. the `map-network-app-nodejs`
dev docker-compose stack) before publishing, build a **local-test** `.mcpb`
bundle instead of the official one. It must never be named or displayed the
same as the real release — Claude Desktop's extension list identifies
extensions by `name`/`display_name`, not filename, so both need to differ,
not just the output path.

## Steps

1. **Temporarily edit `manifest.json`** (under git control, so it's easy to
   revert cleanly):
   - `name`: `mapnetwork-mcp` → `mapnetwork-mcp-localtest`
   - `display_name`: `MapNetwork` → `MapNetwork (Local Test)`
   - Add an `env` block under `server.mcp_config` pointing at the local
     backend:
     ```json
     "mcp_config": {
       "command": "uv",
       "args": ["run", "--directory", "${__dirname}", "mapnetwork_mcp/server.py"],
       "env": {
         "MAPNETWORK_BASE_URL": "http://localhost"
       }
     }
     ```
     Adjust the URL/port to match wherever the local stack's nginx is
     actually listening (see `docker-compose.dev.yml` in
     `map-network-app-nodejs` — by default port 80).

2. **Validate and pack**:
   ```bash
   npx --yes @anthropic-ai/mcpb validate manifest.json
   npx --yes @anthropic-ai/mcpb pack . dist/mapnetwork-mcp-localtest.mcpb
   ```
   The `-localtest` suffix in the output path is not enough by itself (see
   above) — it only matters together with the `name`/`display_name` edit in
   step 1.

3. **Revert `manifest.json`** back to the official version:
   ```bash
   git restore manifest.json
   git status --short   # confirm no unintended diff remains
   ```
   The env/name/display_name overrides only need to exist inside the packed
   `.mcpb` (which is git-ignored, `dist/`), not in the committed source.

4. **Load it in Claude Desktop** (drag-and-drop the `.mcpb`, or via the
   Extensions screen). It will show up as a separate "MapNetwork (Local
   Test)" extension, distinct from any installed official "MapNetwork" one.

## Why

- `MAPNETWORK_BASE_URL` (read in `mapnetwork_mcp/server.py`) is what points
  the client at a backend other than the default `https://mapnetwork.app`.
  There's no other way to set it for a Claude-Desktop-launched extension —
  it isn't inherited from any project `.env` file, since Claude Desktop
  launches the `uv run ...` subprocess with its own environment, not this
  repo's (or `map-network-app-nodejs`'s) shell/dotenv state.
- Reusing the official name/filename risks Claude Desktop's extension list
  showing two indistinguishable "MapNetwork" entries, or a locally-packed
  build silently overwriting/being confused with the real release artifact.
