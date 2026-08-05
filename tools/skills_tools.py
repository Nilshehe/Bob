"""
skills_tools.py
----------------
Ger en LangChain-agent förmågan att:
  1. Lista tillgängliga skills (namn + kort beskrivning, billigt i tokens)
  2. Läsa in en hel SKILL.md när den behövs (dyrt i tokens, görs bara vid träff)
  3. Skapa nya skills (agenten kan lära sig nya rutiner permanent)

Struktur på disk:
  skills/
    docx/
      SKILL.md      <- måste börja med YAML-frontmatter: name + description
      scripts/...   <- valfria hjälpfiler skillen refererar till
    minecraft_datapack/
      SKILL.md

Designidé (samma som Anthropics egen skill-mekanism):
- Agenten ser ALDRIG hela innehållet i alla skills på en gång (för dyrt).
- Den ser bara en lista med "name: description".
- Den läser in full text bara för den/de skills som verkar matcha uppgiften.
"""

import os
import re
from pathlib import Path
from typing import List

from langchain_core.tools import tool

SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", "./skills")).resolve()
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    """Enkel YAML-lite-parser, klarar 'key: value' rader utan externt beroende."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    return meta


def _skill_dirs() -> List[Path]:
    return sorted([p for p in SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists()])


@tool
def list_skills() -> str:
    """Lista alla tillgängliga skills med namn och kort beskrivning.
    Anropa denna FÖRST för att se om något passar uppgiften, innan du gissar själv."""
    dirs = _skill_dirs()
    if not dirs:
        return "Inga skills hittades i " + str(SKILLS_DIR)

    lines = []
    for d in dirs:
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        name = meta.get("name", d.name)
        desc = meta.get("description", "(ingen beskrivning)")
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


@tool
def read_skill(name: str) -> str:
    """Läs in FULLA innehållet i en specifik skill (SKILL.md) givet dess namn eller mappnamn.
    Använd bara när list_skills har visat att den är relevant för uppgiften."""
    # tillåt matchning på både mappnamn och 'name:'-fältet
    for d in _skill_dirs():
        if d.name == name:
            return (d / "SKILL.md").read_text(encoding="utf-8")
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        if meta.get("name") == name:
            return text

    available = ", ".join(p.name for p in _skill_dirs())
    return f"Hittade ingen skill som heter '{name}'. Tillgängliga: {available}"


@tool
def create_skill(folder_name: str, description: str, content: str) -> str:
    """Skapa en ny skill permanent på disk. folder_name blir mappnamnet (t.ex. 'minecraft_datapack'),
    description är en kort mening som avgör NÄR skillen ska triggas, content är hela SKILL.md-texten
    (kan innehålla mer detaljerade instruktioner, exempel, kodmönster etc under frontmatter)."""
    folder_name = re.sub(r"[^a-z0-9_\-]", "_", folder_name.lower())
    skill_dir = SKILLS_DIR / folder_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    body = content.strip()
    # om content redan har frontmatter, rör vi inte till det
    if not body.startswith("---"):
        body = f"---\nname: {folder_name}\ndescription: {description}\n---\n\n{body}"

    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    return f"Skill '{folder_name}' skapad i {skill_dir}"


@tool
def list_skill_files(folder_name: str) -> str:
    """Lista extrafiler (scripts, referenser) som hör till en skill-mapp."""
    skill_dir = SKILLS_DIR / folder_name
    if not skill_dir.exists():
        return f"Ingen sådan skill-mapp: {folder_name}"
    files = [str(p.relative_to(skill_dir)) for p in skill_dir.rglob("*") if p.is_file() and p.name != "SKILL.md"]
    return "\n".join(files) if files else "(inga extra filer)"


ALL_SKILL_TOOLS = [list_skills, read_skill, create_skill, list_skill_files]


# ---------------------------------------------------------------------------
# Exempel: koppla in i en LangChain tool-calling agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # snabbtest utan LLM
    print(create_skill.invoke({
        "folder_name": "villager_trade_cap",
        "description": "Använd när villager-handel i Minecraft ger oändliga/dubbla trades och behöver ett globalt tak per villager.",
        "content": "## Fix\nSätt ett globalt max-antal trades per villager-UUID i en scoreboard, "
                    "inte deduplicering av trade-listan. Kontrollera taket i on_trade-eventet innan "
                    "trade tillåts genomföras.\n",
    }))
    print()
    print(list_skills.invoke({}))
    print()
    print(read_skill.invoke({"name": "villager_trade_cap"}))
