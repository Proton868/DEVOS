"""
Research — Deep Research Agent (Agency OS Master Plan §5).

Multi-step web research agent that:
1. Takes a research question
2. Searches the web for relevant sources
3. Reads and extracts content from each source
4. Synthesizes findings into a structured report
5. Cites all sources with proper attribution

This is a clean-room implementation informed by multi-step deep-research patterns (Hermes-style; historically referenced as Odysseus)
feature concept, not merged from its AGPL-licensed codebase.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.research")

RESEARCH_DIR = Path("data/research")
RESEARCH_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Source:
    """A single research source with extracted content."""
    url:           str
    title:         str = ""
    snippet:       str = ""
    content:       str = ""           # extracted full text
    relevance:     float = 0.0        # 0-1 relevance score
    credibility:   float = 0.5        # 0-1 credibility estimate
    keywords:      list[str] = field(default_factory=list)
    retrieved_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet[:200],
            "content": self.content[:2000],
            "relevance": self.relevance,
            "credibility": self.credibility,
            "keywords": self.keywords,
            "retrieved_at": self.retrieved_at.isoformat(),
        }


@dataclass
class Citation:
    """A citation in the research report."""
    id:         str
    source_url: str
    text:       str                # the cited text from the source
    context:    str = ""           # how it's used in the report
    page:       Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_url": self.source_url,
            "text": self.text[:500],
            "context": self.context[:200],
            "page": self.page,
        }


@dataclass
class ResearchReport:
    """A complete research report."""
    report_id:       str
    question:        str
    summary:         str = ""
    sections:        list[dict] = field(default_factory=list)
    sources:         list[Source] = field(default_factory=list)
    citations:       list[Citation] = field(default_factory=list)
    confidence:      float = 0.0           # 0-1 overall confidence
    gaps:            list[str] = field(default_factory=list)  # known gaps
    created_at:      datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    search_queries:  list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "question": self.question,
            "summary": self.summary,
            "sections": self.sections,
            "sources": [s.to_dict() for s in self.sources],
            "citations": [c.to_dict() for c in self.citations],
            "confidence": self.confidence,
            "gaps": self.gaps,
            "created_at": self.created_at.isoformat(),
            "search_queries": self.search_queries,
        }

    def save(self):
        """Persist the report to disk."""
        path = RESEARCH_DIR / f"{self.report_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), default=str, indent=2))
        tmp.replace(path)

    @classmethod
    def load(cls, report_id: str) -> Optional["ResearchReport"]:
        path = RESEARCH_DIR / f"{report_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            report = cls(
                report_id=data["report_id"],
                question=data["question"],
                summary=data.get("summary", ""),
                sections=data.get("sections", []),
                confidence=data.get("confidence", 0.0),
                gaps=data.get("gaps", []),
                search_queries=data.get("search_queries", []),
            )
            report.sources = [Source(**s) for s in data.get("sources", [])]
            report.citations = [Citation(**c) for c in data.get("citations", [])]
            return report
        except Exception as e:
            logger.warning(f"[research] failed to load report {report_id}: {e}")
            return None


RESEARCH_SYSTEM_PROMPT = """You are a Deep Research agent for DevOS. Your job is to:
1. Understand the research question thoroughly
2. Generate effective search queries
3. Analyze and synthesize findings from multiple sources
4. Produce structured, well-cited research reports
5. Identify gaps and uncertainties honestly

Always cite your sources. Never fabricate information. If you're uncertain, say so."""


class DeepResearchAgent:
    """Multi-step web research agent."""

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider
        self.model = model

    async def research(self, question: str, max_sources: int = 5,
                       depth: str = "standard") -> ResearchReport:
        """Conduct deep research on a question.
        depth: "quick" (3 sources), "standard" (5), "deep" (10)"""
        report_id = str(uuid.uuid4())
        report = ResearchReport(report_id=report_id, question=question)

        # Adjust max sources based on depth
        if depth == "quick":
            max_sources = 3
        elif depth == "deep":
            max_sources = 10

        try:
            # Step 1: Generate search queries
            queries = await self._generate_queries(question)
            report.search_queries = queries

            # Step 2: Search and collect sources
            for query in queries[:max_sources]:
                sources = await self._search(query)
                report.sources.extend(sources)
                if len(report.sources) >= max_sources * 2:
                    break

            # Deduplicate sources
            seen = set()
            unique = []
            for s in report.sources:
                if s.url not in seen:
                    seen.add(s.url)
                    unique.append(s)
            report.sources = unique[:max_sources]

            # Step 3: Synthesize findings
            report = await self._synthesize(report)

            # Step 4: Save
            report.save()

        except Exception as e:
            logger.error(f"[research] research failed: {e}")
            report.summary = f"Research failed: {e}"

        return report

    async def _generate_queries(self, question: str) -> list[str]:
        """Generate effective search queries for the research question."""
        try:
            from brain.llm import BrainLLM
            brain = BrainLLM(provider=self.provider, model=self.model)
            prompt = f"""Generate 3-5 effective search queries for this research question:
QUESTION: {question}

Respond with JSON: {{"queries": ["query1", "query2", ...]}}"""
            response = await brain._call(self.provider or "ollama", [
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
            data = json.loads(response.strip())
            return data.get("queries", [question])
        except Exception as e:
            logger.warning(f"[research] query generation failed: {e}")
            return [question]

    async def _search(self, query: str) -> list[Source]:
        """Search the web and return structured sources."""
        try:
            from execution.search import search_web
            results = await search_web(query, max_results=5)
            sources = []
            for r in results:
                sources.append(Source(
                    url=r.get("url", ""),
                    title=r.get("title", ""),
                    snippet=r.get("snippet", r.get("content", "")[:200]),
                    content=r.get("content", ""),
                    keywords=query.split(),
                ))
            return sources
        except Exception as e:
            logger.warning(f"[research] search failed for '{query}': {e}")
            return []

    async def _synthesize(self, report: ResearchReport) -> ResearchReport:
        """Synthesize all sources into a structured report."""
        try:
            from brain.llm import BrainLLM
            brain = BrainLLM(provider=self.provider, model=self.model)

            sources_text = "\n\n".join(
                f"[{i+1}] {s.title}\nURL: {s.url}\n{s.content[:500]}"
                for i, s in enumerate(report.sources)
            )

            prompt = f"""Synthesize a research report from these sources.

QUESTION: {report.question}

SOURCES:
{sources_text}

Respond with JSON:
{{
  "summary": "2-3 sentence executive summary",
  "sections": [
    {{"heading": "Section title", "content": "section text with [source_number] citations"}}
  ],
  "citations": [
    {{"id": "cit1", "source_url": "url", "text": "cited text", "context": "how used"}}
  ],
  "confidence": 0.0-1.0,
  "gaps": ["known gaps or uncertainties"]
}}"""

            response = await brain._call(self.provider or "ollama", [
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])

            data = json.loads(response.strip())
            report.summary = data.get("summary", "")
            report.sections = data.get("sections", [])
            report.confidence = float(data.get("confidence", 0.5))
            report.gaps = data.get("gaps", [])

            for cit in data.get("citations", []):
                report.citations.append(Citation(
                    id=cit.get("id", ""),
                    source_url=cit.get("source_url", ""),
                    text=cit.get("text", ""),
                    context=cit.get("context", ""),
                ))

        except Exception as e:
            logger.warning(f"[research] synthesis failed: {e}")
            report.summary = "Synthesis failed — see raw sources."

        return report


async def deep_research(question: str, max_sources: int = 5,
                        depth: str = "standard",
                        provider: Optional[str] = None,
                        model: Optional[str] = None) -> ResearchReport:
    """Convenience: run deep research on a question."""
    agent = DeepResearchAgent(provider, model)
    return await agent.research(question, max_sources, depth)


def list_reports(limit: int = 50) -> list[dict]:
    """List recent research reports."""
    reports = []
    for f in sorted(RESEARCH_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            reports.append({
                "report_id": data.get("report_id"),
                "question": data.get("question", "")[:100],
                "summary": data.get("summary", "")[:200],
                "source_count": len(data.get("sources", [])),
                "confidence": data.get("confidence", 0),
                "created_at": data.get("created_at", ""),
            })
            if len(reports) >= limit:
                break
        except Exception:
            pass
    return reports