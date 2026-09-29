"""Knowledge Graph extractor — uses LLM to extract entity-relation-entity triples from a transcript.

Reads a transcript JSON, sends the text to the LLM configured in .env, and writes
a structured triples JSON file. If Neo4j is configured in .env, also writes to Neo4j.

Usage:
    python kg_extractor.py                              # process all transcripts
    python kg_extractor.py "static/txt/xxx_en.json"     # process one file
    python kg_extractor.py --force                      # overwrite existing output

LLM config is read from .env (same as streamlit_app.py and video_analyzer.py).
Neo4j config is optional — if NEO4J_URI is set, triples are also written to Neo4j.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEXT_DIR = REPO / "static" / "txt"


# ---------------------------------------------------------------- Config

def load_env(env_path: Path = REPO / ".env") -> dict[str, str]:
    config: dict[str, str] = {}
    if not env_path.exists():
        return config
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip().strip('"').strip("'")
    return config


CFG = load_env()
LLM_API_KEY = CFG.get("LLM_API_KEY", "")
LLM_API_BASE = CFG.get("LLM_API_BASE", "https://api.deepseek.com/v1")
LLM_MODEL = CFG.get("LLM_MODEL", "deepseek-chat")

# Neo4j (optional)
NEO4J_URI = CFG.get("NEO4J_URI", "")
NEO4J_USER = CFG.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = CFG.get("NEO4J_PASSWORD", "")


# ---------------------------------------------------------------- LLM call

def call_llm(prompt: str, *, max_tokens: int = 4096, temperature: float = 0.2) -> str:
    if not LLM_API_KEY:
        raise SystemExit("LLM_API_KEY is not set in .env")

    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{LLM_API_BASE.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------- Prompt

KG_PROMPT = """\
You are a knowledge graph extraction expert. Analyze the following video transcript and extract entity-relation-entity triples.

Rules:
1. Each triple has: subject (entity), relation (predicate), object (entity or value).
2. Entities should be specific and normalized (e.g., "neural network" not "a neural network").
3. Relations should be concise verbs or phrases (e.g., "is a type of", "uses", "requires", "enables", "consists of", "is used for").
4. Extract 20-60 triples covering the key concepts, relationships, and facts in the transcript.
5. Include different relation types: definitions, compositions, applications, prerequisites, examples, properties.
6. Entity types to tag: concept, person, method, application, property, example.

Output ONLY a JSON object with this exact structure (no markdown fences, no extra text):

{
  "entities": [
    {"name": "entity name", "type": "concept|person|method|application|property|example"}
  ],
  "triples": [
    {"subject": "entity name", "relation": "relation phrase", "object": "entity name or value"}
  ]
}

Every subject and object in "triples" must appear in "entities".

--- TRANSCRIPT ---

{transcript}

--- END ---
"""


# ---------------------------------------------------------------- Extraction

def extract_kg(transcript_path: Path) -> dict:
    """Extract knowledge graph triples from a transcript using LLM."""
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    full_text = data.get("text", "")
    if not full_text and data.get("segments"):
        full_text = "".join(seg["text"] for seg in data["segments"])

    if not full_text.strip():
        raise ValueError(f"Empty transcript: {transcript_path.name}")

    # Cap at ~30k chars
    if len(full_text) > 30000:
        full_text = full_text[:30000] + "\n... (truncated)"

    prompt = KG_PROMPT.replace("{transcript}", full_text)

    print(f"  Calling LLM ({LLM_MODEL}) ...", flush=True)
    started = time.time()
    raw_reply = call_llm(prompt, max_tokens=4096, temperature=0.2)
    elapsed = time.time() - started
    print(f"  LLM replied in {elapsed:.1f}s ({len(raw_reply)} chars)", flush=True)

    # Parse JSON
    cleaned = raw_reply.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(cleaned[start:end])
        else:
            raise ValueError(f"LLM reply is not valid JSON:\n{raw_reply[:500]}")

    # Validate
    entities = result.get("entities", [])
    triples = result.get("triples", [])
    entity_names = {e["name"] for e in entities}

    # Filter triples whose subject/object are not in entities
    valid_triples = []
    for t in triples:
        if t.get("subject") in entity_names and t.get("object") in entity_names:
            valid_triples.append(t)
        elif t.get("subject") in entity_names:
            # Object might be a value, add it as an entity
            entities.append({"name": t["object"], "type": "value"})
            entity_names.add(t["object"])
            valid_triples.append(t)

    result["entities"] = entities
    result["triples"] = valid_triples
    result["_source"] = transcript_path.name
    result["_model"] = LLM_MODEL
    result["_extracted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    result["_entity_count"] = len(entities)
    result["_triple_count"] = len(valid_triples)

    return result


# ---------------------------------------------------------------- Neo4j (optional)

def write_to_neo4j(kg_data: dict, video_name: str) -> None:
    """Write triples to Neo4j if configured. Uses HTTP API (no driver needed)."""
    if not NEO4J_URI or not NEO4J_PASSWORD:
        return

    import base64
    auth = base64.b64encode(f"{NEO4J_USER}:{NEO4J_PASSWORD}".encode()).decode()

    # Build Cypher statements
    statements = []

    # Create entities
    for entity in kg_data.get("entities", []):
        name = entity["name"].replace("'", "\\'")
        etype = entity.get("type", "concept")
        stmt = (
            f"MERGE (e:Entity {{name: '{name}'}}) "
            f"SET e.type = '{etype}', e.video = '{video_name}'"
        )
        statements.append({"statement": stmt})

    # Create relationships
    for triple in kg_data.get("triples", []):
        subj = triple["subject"].replace("'", "\\'")
        rel = triple["relation"].replace("'", "\\'").replace(" ", "_").upper()
        obj = triple["object"].replace("'", "\\'")
        stmt = (
            f"MATCH (a:Entity {{name: '{subj}'}}) "
            f"MATCH (b:Entity {{name: '{obj}'}}) "
            f"MERGE (a)-[r:{rel}]->(b) "
            f"SET r.video = '{video_name}'"
        )
        statements.append({"statement": stmt})

    if not statements:
        return

    payload = json.dumps({"statements": statements}).encode("utf-8")
    url = f"{NEO4J_URI.rstrip('/')}/db/neo4j/tx/commit"

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            errors = result.get("errors", [])
            if errors:
                print(f"  Neo4j errors: {errors[:3]}", flush=True)
            else:
                print(f"  Neo4j: wrote {len(statements)} statements", flush=True)
    except Exception as exc:
        print(f"  Neo4j write failed: {exc}", flush=True)


# ---------------------------------------------------------------- Output path

def output_path_for(transcript_path: Path) -> Path:
    stem = transcript_path.stem
    return transcript_path.parent / f"{stem}_kg.json"


# ---------------------------------------------------------------- Main

def main() -> None:
    import argparse

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Extract knowledge graph from video transcripts")
    parser.add_argument("transcript", nargs="?", help="Transcript JSON path (default: all in static/txt)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output")
    args = parser.parse_args()

    if not LLM_API_KEY:
        print("ERROR: LLM_API_KEY is not set in .env")
        sys.exit(1)

    print(f"LLM: {LLM_MODEL} @ {LLM_API_BASE}")
    if NEO4J_URI:
        print(f"Neo4j: {NEO4J_URI} (will write triples)")
    else:
        print("Neo4j: not configured (JSON only)")

    if args.transcript:
        targets = [Path(args.transcript)]
    else:
        targets = sorted(TEXT_DIR.glob("*_en.json")) + sorted(
            p for p in TEXT_DIR.glob("*.json")
            if "_analysis" not in p.name and "_kg" not in p.name and "_en" not in p.stem
        )
        seen: set[str] = set()
        unique = []
        for t in targets:
            if t.name not in seen:
                seen.add(t.name)
                unique.append(t)
        targets = unique

    if not targets:
        print("No transcript JSON files found.")
        sys.exit(1)

    print(f"Found {len(targets)} transcript(s).\n")

    for i, tpath in enumerate(targets, 1):
        out = output_path_for(tpath)
        if out.exists() and not args.force:
            print(f"[{i}/{len(targets)}] SKIP (exists): {out.name}")
            continue

        print(f"[{i}/{len(targets)}] Extracting KG: {tpath.name}")
        try:
            kg = extract_kg(tpath)
            out.write_text(json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  -> Saved: {out.name} ({kg['_entity_count']} entities, {kg['_triple_count']} triples)")

            # Optional Neo4j write
            video_name = tpath.stem.replace("_en", "").replace(".json", "")
            write_to_neo4j(kg, video_name)

        except Exception as exc:
            print(f"  -> FAILED: {exc}")

    print("\nDone.")


if __name__ == "__main__":
    main()
