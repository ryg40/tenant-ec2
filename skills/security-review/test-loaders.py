#!/usr/bin/env python3
"""Offline harness checks. Use temporary clones, homes, and an API stub."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]


def check_claude(clone, home):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            response = {
                "id": "msg_fixture", "type": "message", "role": "assistant",
                "model": "claude-sonnet-4-5-20250929",
                "content": [{"type": "text", "text": "Loader fixture response."}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {
            "PATH": os.environ["PATH"], "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        command = [shutil.which("claude"), "--setting-sources", "project,user", "-p", "/security-review",
                   "--output-format", "json", "--no-session-persistence", "--strict-mcp-config",
                   "--mcp-config", '{"mcpServers":{}}', "--tools", "", "--permission-mode", "dontAsk",
                   "--model", "claude-sonnet-4-5-20250929", "--effort", "xhigh"]
        for collision in (False, True):
            if collision:
                skill = home / ".claude/skills/security-review"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    "---\nname: security-review\ndescription: Collision fixture.\n---\nHOME_COLLISION_MARKER\n")
            requests.clear()
            result = subprocess.run(command, cwd=clone, env=env, capture_output=True, timeout=60)
            text = json.dumps(requests)
            assert result.returncode == 0 and requests
            assert ("# Advisory install review" in text) is not collision
            assert ("HOME_COLLISION_MARKER" in text) is collision
        print("Claude loader: pass (project expansion, personal skill wins collision, local API stub)")
    finally:
        server.shutdown()
        server.server_close()


def check_pi(clone, home, package):
    code = r"""
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const [cwd,agentDir,pkg]=process.argv.slice(1);
const {loadSkills}=await import(pathToFileURL(path.join(pkg,'dist/core/skills.js')));
const {loadPromptTemplates,expandPromptTemplate}=await import(pathToFileURL(path.join(pkg,'dist/core/prompt-templates.js')));
let result=loadSkills({cwd,agentDir,skillPaths:[],includeDefaults:true});
assert.equal(result.diagnostics.length,0);
assert.equal(result.skills.length,1);
assert.equal(result.skills[0].name,'security-review');
assert.match(fs.readFileSync(result.skills[0].filePath,'utf8'),/# Advisory install review/);
const prompts=loadPromptTemplates({cwd,agentDir,promptPaths:[],includeDefaults:true});
assert.equal(prompts.diagnostics.length,0);
assert.ok(expandPromptTemplate('/security-review options',prompts.templates).includes('Read `skills/security-review/SKILL.md`'));
fs.mkdirSync(path.join(agentDir,'skills/security-review'),{recursive:true});
fs.writeFileSync(path.join(agentDir,'skills/security-review/SKILL.md'),'---\nname: security-review\ndescription: Collision fixture.\n---\nHOME_COLLISION\n');
result=loadSkills({cwd,agentDir,skillPaths:[],includeDefaults:true});
assert.equal(result.diagnostics[0].type,'collision');
assert.equal(result.skills[0].filePath,path.join(agentDir,'skills/security-review/SKILL.md'));
assert.ok(expandPromptTemplate('/security-review',prompts.templates).includes('Read `skills/security-review/SKILL.md`'));
console.log('Pi loader: pass (project skill, short command, user skill wins collision, path alias)');
"""
    result = subprocess.run(["node", "--input-type=module", "-e", code, str(clone), str(home), str(package)],
                            env={"PATH": os.environ["PATH"], "HOME": str(home)}, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0
    print(result.stdout.strip())


def main():
    if len(sys.argv) != 2:
        print("usage: python3 skills/security-review/test-loaders.py PI_PACKAGE_DIRECTORY")
        return 2
    with tempfile.TemporaryDirectory(prefix="review-loader-") as directory:
        work = Path(directory)
        clone, home, agent = work / "clone", work / "home", work / "agent"
        clone.mkdir()
        home.mkdir()
        agent.mkdir()
        shutil.copytree(ROOT / "skills/security-review", clone / "skills/security-review")
        subprocess.run(["git", "init", "-q", str(clone)], check=True, capture_output=True)
        command = ["sh", "skills/security-review/install.sh"]
        for _ in range(2):
            subprocess.run(command, cwd=clone, check=True, capture_output=True)
        for name in (".claude/skills/security-review/SKILL.md", ".pi/skills/security-review/SKILL.md"):
            assert (clone / name).read_bytes() == (ROOT / "skills/security-review/SKILL.md").read_bytes()
        check_claude(clone, home)
        check_pi(clone, agent, Path(sys.argv[1]).resolve())
    print("harness loader checks: pass (offline fixtures only)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("harness loader checks: FAIL (no input or captured request shown)")
        sys.exit(1)
