# Releasing a new version

This project's version number is declared in **three places** that must stay in sync:

| File | Field(s) |
|---|---|
| `pyproject.toml` | `[project] version` |
| `server.json` | `version`, `packages[0].version` |
| `manifest.json` | `version` |

## Steps

1. **Bump the version** in all three files above (same value).

2. **(Optional) Local build sanity check** — the real publish now happens
   unattended in CI (see step 3), so this is just a pre-flight check to catch
   build errors before tagging:
   ```bash
   rm -f dist/*.whl dist/*.tar.gz
   .venv/Scripts/python -m build
   .venv/Scripts/python -m twine check dist/mapnetwork_mcp-<version>*
   ```
   Use the version-specific glob, not `dist/*` — a leftover `.mcpb` from a
   previous release sits in the same `dist/` folder and `twine check` errors
   out on it ("Unknown distribution format") even though the actual wheel/
   sdist are fine.

3. **Commit, tag, and push** (moved up — pushing the tag is what triggers the release)
   ```bash
   git add pyproject.toml server.json manifest.json mapnetwork_mcp/server.py  # plus any other source files changed for this release
   git commit -m "Bump version to <version>"
   git push origin main
   git tag -a v<version> -m "v<version>"
   git push origin v<version>
   ```
   Pushing the `v<version>` tag triggers `.github/workflows/workflow.yml`,
   which builds the package and publishes it to PyPI via [Trusted
   Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC) —
   **no `twine upload`, no PyPI API token needed anymore.** The trusted
   publisher is registered on PyPI's project settings (repo:
   `toruproject/mapnetwork-mcp`, workflow: `workflow.yml`); if the workflow
   filename ever changes, that registration must be updated to match.

4. **Confirm the release landed**
   Watch the workflow run in the repo's Actions tab, then verify PyPI has it:
   ```bash
   curl -s https://pypi.org/pypi/mapnetwork-mcp/json | grep -o '"<version>"'
   ```
   If the workflow fails after the tag was already pushed, fix the issue and
   push a new patch version — PyPI rejects re-uploading a version that
   already exists, and tags shouldn't be deleted/reused once pushed.

5. **Publish to the MCP registry**
   ```bash
   ./mcp-publisher.exe validate server.json
   ./mcp-publisher.exe login github   # only needed if the session expired
   ./mcp-publisher.exe publish server.json
   ```
   The registry entry's `packages[0].version` must match the version actually live on PyPI (the registry does not host the code itself).

   ⚠️ `login github` opens an interactive device-confirmation flow in the
   browser — it cannot be automated or run on someone's behalf. Run it
   yourself in your own terminal (not delegated to an agent) and confirm
   it's done before continuing to `publish`.

6. **Rebuild the MCPB bundle**
   ```bash
   npx --yes @anthropic-ai/mcpb validate manifest.json
   npx --yes @anthropic-ai/mcpb pack . dist/mapnetwork-mcp.mcpb
   ```
   The output filename is literally `mapnetwork-mcp.mcpb` — **not**
   version-suffixed (the `mcpb pack` tool's own summary output shows a
   version-suffixed "Archive Details > filename" field, which is just a
   display label and does not match the actual file written to disk). Use
   `dist/mapnetwork-mcp.mcpb` verbatim in step 7, not a guessed
   `mapnetwork-mcp-<version>.mcpb`.

7. **Create the GitHub Release** with the `.mcpb` bundle attached
   ```bash
   gh release create v<version> dist/mapnetwork-mcp.mcpb --title "v<version>" --notes "..."
   ```

## Notes

- `.mcpregistry_github_token` / `.mcpregistry_registry_token` hold the `mcp-publisher` login session — they are git-ignored and must never be committed.
- `dist/` is git-ignored; build artifacts and the `.mcpb` bundle are distributed only via PyPI / the GitHub Release, not via the repository. Old files from a
  previous release aren't cleaned up automatically (step 2 only removes
  `*.whl`/`*.tar.gz`) — the stray `.mcpb` from the last run stays there, which
  is exactly what trips up the `twine check dist/*` glob above.