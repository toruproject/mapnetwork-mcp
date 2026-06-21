# Releasing a new version

This project's version number is declared in **three places** that must stay in sync:

| File | Field(s) |
|---|---|
| `pyproject.toml` | `[project] version` |
| `server.json` | `version`, `packages[0].version` |
| `manifest.json` | `version` |

## Steps

1. **Bump the version** in all three files above (same value).

2. **Build the PyPI package**
   ```bash
   rm -f dist/*.whl dist/*.tar.gz
   .venv/Scripts/python -m build
   .venv/Scripts/python -m twine check dist/*
   ```

3. **Upload to PyPI**
   ```bash
   .venv/Scripts/python -m twine upload dist/mapnetwork_mcp-<version>*
   ```
   Requires a PyPI API token (via `~/.pypirc` or `TWINE_USERNAME=__token__` / `TWINE_PASSWORD=...`).

4. **Publish to the MCP registry**
   ```bash
   ./mcp-publisher.exe validate server.json
   ./mcp-publisher.exe login github   # only needed if the session expired
   ./mcp-publisher.exe publish server.json
   ```
   The registry entry's `packages[0].version` must match the version actually live on PyPI (the registry does not host the code itself).

5. **Rebuild the MCPB bundle**
   ```bash
   npx --yes @anthropic-ai/mcpb validate manifest.json
   npx --yes @anthropic-ai/mcpb pack . dist/mapnetwork-mcp.mcpb
   ```

6. **Commit, tag, and push**
   ```bash
   git add pyproject.toml server.json manifest.json
   git commit -m "Bump version to <version>"
   git push origin main
   git tag -a v<version> -m "v<version>"
   git push origin v<version>
   ```

7. **Create the GitHub Release** with the `.mcpb` bundle attached
   ```bash
   gh release create v<version> dist/mapnetwork-mcp.mcpb --title "v<version>" --notes "..."
   ```

## Notes

- `.mcpregistry_github_token` / `.mcpregistry_registry_token` hold the `mcp-publisher` login session — they are git-ignored and must never be committed.
- `dist/` is git-ignored; build artifacts and the `.mcpb` bundle are distributed only via PyPI / the GitHub Release, not via the repository.