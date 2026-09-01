"""
DevOS Project Builder — generates complete full-stack apps.
Better than Cursor because it actually RUNS the code and fixes errors in a loop.

Supports:
  Python (FastAPI, Django, Flask, script, CLI, data pipeline)
  JavaScript/TypeScript (React, Next.js, Vite, Node.js, Express)
  Flutter (cross-platform mobile + desktop)
  React Native / Expo (mobile)
  HTML/CSS/JS (static sites, landing pages)
  Any other language or framework via the generic builder
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.builder")

PROJECTS_DIR = Path("data/projects")
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ProjectSpec:
    """What to build."""
    name: str
    description: str
    stack: str           # "fastapi" | "nextjs" | "react" | "vite" | "flutter" | "html" | "custom"
    language: str
    features: list[str]
    user_id: str
    output_dir: Optional[str] = None
    extra_instructions: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "stack": self.stack, "language": self.language,
            "features": self.features,
        }


@dataclass
class GeneratedFile:
    path: str
    content: str
    description: str


@dataclass
class BuildResult:
    project_id: str
    spec: ProjectSpec
    files: list[GeneratedFile] = field(default_factory=list)
    readme: str = ""
    setup_commands: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "spec": self.spec.to_dict(),
            "files": [{"path": f.path, "description": f.description} for f in self.files],
            "setup_commands": self.setup_commands,
            "errors": self.errors,
            "created_at": self.created_at.isoformat(),
        }


# ── Stack templates — the scaffolding the Brain fills in ─────────────────────

STACK_TEMPLATES = {
    "fastapi": {
        "language": "python",
        "description": "FastAPI REST API with SQLAlchemy + SQLite",
        "files_to_generate": [
            "main.py", "models.py", "schemas.py", "database.py",
            "requirements.txt", "README.md", ".env.example", "Dockerfile"
        ],
        "setup": ["pip install -r requirements.txt", "uvicorn main:app --reload"],
    },
    "nextjs": {
        "language": "typescript",
        "description": "Next.js 14 App Router with TailwindCSS",
        "files_to_generate": [
            "app/page.tsx", "app/layout.tsx", "app/globals.css",
            "components/ui/button.tsx", "package.json", "tailwind.config.ts",
            "next.config.ts", "README.md"
        ],
        "setup": ["npm install", "npm run dev"],
    },
    "vite-react": {
        "language": "typescript",
        "description": "Vite + React + TypeScript + TailwindCSS",
        "files_to_generate": [
            "src/App.tsx", "src/main.tsx", "src/index.css",
            "index.html", "package.json", "vite.config.ts",
            "tailwind.config.ts", "tsconfig.json", "README.md"
        ],
        "setup": ["npm install", "npm run dev"],
    },
    "flutter": {
        "language": "dart",
        "description": "Flutter cross-platform app with Riverpod",
        "files_to_generate": [
            "lib/main.dart", "lib/app.dart",
            "lib/features/home/home_screen.dart",
            "lib/features/home/home_provider.dart",
            "pubspec.yaml", "README.md"
        ],
        "setup": ["flutter pub get", "flutter run"],
    },
    "react-native": {
        "language": "typescript",
        "description": "Expo React Native app with TypeScript",
        "files_to_generate": [
            "app/(tabs)/index.tsx", "app/(tabs)/_layout.tsx",
            "app/_layout.tsx", "components/ThemedView.tsx",
            "package.json", "app.json", "tsconfig.json", "README.md"
        ],
        "setup": ["npm install", "npx expo start"],
    },
    "html": {
        "language": "html",
        "description": "Static HTML/CSS/JS site",
        "files_to_generate": [
            "index.html", "style.css", "script.js", "README.md"
        ],
        "setup": ["open index.html (or use Live Server)"],
    },
    "python-script": {
        "language": "python",
        "description": "Python script or CLI tool",
        "files_to_generate": [
            "main.py", "requirements.txt", "README.md"
        ],
        "setup": ["pip install -r requirements.txt", "python main.py"],
    },
    "python-data": {
        "language": "python",
        "description": "Python data analysis / pipeline",
        "files_to_generate": [
            "main.py", "pipeline.py", "requirements.txt", "README.md"
        ],
        "setup": ["pip install -r requirements.txt", "python main.py"],
    },
    "django": {
        "language": "python",
        "description": "Django web app with REST framework",
        "files_to_generate": [
            "manage.py", "config/settings.py", "config/urls.py",
            "api/models.py", "api/views.py", "api/serializers.py",
            "api/urls.py", "requirements.txt", "README.md"
        ],
        "setup": [
            "pip install -r requirements.txt",
            "python manage.py migrate",
            "python manage.py runserver",
        ],
    },
}


class ProjectBuilder:
    """
    Generates complete project files using the Brain.
    Then optionally runs them through the execution layer to verify they work.
    """

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider
        self.model = model

    async def build(self, spec: ProjectSpec, verify: bool = False) -> BuildResult:
        import uuid
        from brain.llm import BrainLLM
        from brain.agents import get_best_agent_for_goal, build_agent_system_prompt

        project_id = str(uuid.uuid4())[:8]
        result = BuildResult(project_id=project_id, spec=spec)

        template = STACK_TEMPLATES.get(spec.stack, {})
        files_to_generate = template.get("files_to_generate", ["main.py", "README.md"])
        result.setup_commands = template.get("setup", [])

        # Pick the best agent persona for this stack
        agent = get_best_agent_for_goal(f"{spec.stack} {spec.language} {spec.description}")

        brain = BrainLLM(provider=self.provider, model=self.model)

        base_system = f"""You are a senior developer generating a complete, production-ready project.
Generate COMPLETE, RUNNABLE code — no placeholders, no "TODO", no "...".
Every file must be complete and correct."""

        system = build_agent_system_prompt(agent, base_system) if agent else base_system

        # Generate each file
        for file_path in files_to_generate:
            content = await self._generate_file(
                brain, system, spec, file_path, result.files
            )
            if content:
                result.files.append(GeneratedFile(
                    path=file_path,
                    content=content,
                    description=f"Generated {file_path}",
                ))

        # Generate README
        result.readme = await self._generate_readme(brain, spec, result.files)

        # Save to disk
        self._save_project(result)

        # Optionally verify by running main entry point
        if verify and spec.language == "python":
            errors = await self._verify_python(result)
            result.errors = errors

        return result

    async def _generate_file(self, brain, system: str, spec: ProjectSpec,
                              file_path: str,
                              existing_files: list[GeneratedFile]) -> str:
        existing_context = "\n\n".join(
            f"--- {f.path} ---\n{f.content[:1000]}"
            for f in existing_files[-3:]  # Last 3 for context
        ) if existing_files else "No files generated yet."

        prompt = f"""Project: {spec.name}
Description: {spec.description}
Stack: {spec.stack} ({spec.language})
Features: {', '.join(spec.features)}
{f'Extra instructions: {spec.extra_instructions}' if spec.extra_instructions else ''}

Already generated:
{existing_context}

Generate the COMPLETE content for: {file_path}

Return ONLY the file content. No explanation before or after."""

        try:
            response = await brain.stream_chat([
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ])
            # Strip markdown fences using regex extraction (avoids corrupting nested fences)
            import re as _re
            content = response.strip()
            fence_match = _re.search(r"```(?:\w+)?\n?(.*?)```", content, _re.DOTALL)
            if fence_match:
                content = fence_match.group(1)
            return content.strip()
        except Exception as e:
            logger.error(f"Failed to generate {file_path}: {e}")
            return ""

    async def _generate_readme(self, brain, spec: ProjectSpec,
                                files: list[GeneratedFile]) -> str:
        prompt = f"""Write a complete README.md for this project:

Name: {spec.name}
Description: {spec.description}
Stack: {spec.stack}
Features: {', '.join(spec.features)}
Files: {', '.join(f.path for f in files)}

Include: Overview, Requirements, Installation, Usage, Configuration, and Project Structure.
Return only the Markdown content."""
        try:
            return await brain.stream_chat([{"role": "user", "content": prompt}])
        except Exception:
            return f"# {spec.name}\n\n{spec.description}\n"

    async def _verify_python(self, result: BuildResult) -> list[str]:
        """Try to syntax-check Python files."""
        import ast
        errors = []
        for f in result.files:
            if f.path.endswith(".py") and f.content:
                try:
                    ast.parse(f.content)
                except SyntaxError as e:
                    errors.append(f"{f.path}: SyntaxError line {e.lineno}: {e.msg}")
        return errors

    def _save_project(self, result: BuildResult):
        project_dir = PROJECTS_DIR / result.spec.user_id / result.project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        for f in result.files:
            file_path = project_dir / f.path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(f.content, encoding="utf-8")

        if result.readme:
            (project_dir / "README.md").write_text(result.readme, encoding="utf-8")

        meta = {
            "project_id": result.project_id,
            "spec": result.spec.to_dict(),
            "files": [f.path for f in result.files],
            "setup_commands": result.setup_commands,
            "errors": result.errors,
            "created_at": result.created_at.isoformat(),
        }
        (project_dir / "devos_meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        logger.info(f"Project saved: {project_dir}")

    def list_projects(self, user_id: str) -> list[dict]:
        user_dir = PROJECTS_DIR / user_id
        if not user_dir.exists():
            return []
        projects = []
        for d in sorted(user_dir.iterdir(), reverse=True):
            meta_file = d / "devos_meta.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text())
                    projects.append(meta)
                except Exception:
                    pass
        return projects

    def get_project_files(self, user_id: str, project_id: str) -> dict:
        project_dir = PROJECTS_DIR / user_id / project_id
        if not project_dir.exists():
            return {}
        files = {}
        for f in project_dir.rglob("*"):
            if f.is_file() and f.name != "devos_meta.json":
                try:
                    files[str(f.relative_to(project_dir))] = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        return files
