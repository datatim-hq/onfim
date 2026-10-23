import re

import anyio

from mem.mcp_server import TEXTS, build_mcp, texts

CYR = re.compile(r"[А-Яа-яЁё]")


def test_languages_cover_the_same_texts():
    """Release stage 2: English and Russian MCP descriptions have the same set of keys; the English ones have no Cyrillic."""
    assert set(TEXTS["en"]) == set(TEXTS["ru"])
    assert [k for k, v in TEXTS["en"].items() if CYR.search(v)] == []


def test_tool_descriptions_follow_language():
    """Release stage 2: MEM_MCP_LANG picks the language of tool descriptions and instructions; an unknown language means English."""
    def described(lang):
        mcp = build_mcp("ann.work", None, "work", lang)
        tools = {t.name: t.description for t in anyio.run(mcp.list_tools)}
        return mcp.instructions, tools

    instr, tools = described("en")
    assert "ann.work" in instr and "decision — a decision taken" in instr
    assert tools["remember"].startswith("Remember a fact.") and "source_scope='dev'" in tools["remember"]
    assert len(tools) == 16 and not any(CYR.search(d) for d in tools.values())
    instr_ru, tools_ru = described("ru")
    assert instr_ru.startswith("Долговременная память") and tools_ru["stats"].startswith("Статистика")
    assert described("de")[1] == tools
    assert "crm_mirror" in texts("en", "team")["remember"]
