"""M-E1: choosing the embedding model on the canaries (07).

Metrics:
  gap      = mean similarity of paraphrases of one fact (c301-c303) minus mean similarity of different facts
             built on one sentence template (c701-c703). Higher is better (a small model scored 0.97 on the "three estimates").
  person   = mean similarity of different facts about one person (c501-c503), for reference.
  R@10/MRR = dense retrieval: 24 questions in other words over 42 canaries (Russian, like the canaries).
Run: docker run --rm -v mem_models:/models mem-api:test \\
  python tools/embed_bench.py tests/fixtures/canaries.json intfloat/multilingual-e5-small:384 ...
"""
from __future__ import annotations

import itertools
import json
import sys
import time

import numpy as np

from mem.embedder import build

QUERIES = [
    ("Когда Зябликову удобно созваниваться?", {"c101"}),
    ("Кто из бухгалтеров общается только в письменном виде?", {"c102"}),
    ("Какой бюджет у ремонта дачи?", {"c103"}),
    ("Когда проходит еженедельный созвон группы по закупкам?", {"c104"}),
    ("Что решили по смете на кровлю с Синицыным?", {"c105"}),
    ("Кто прислал счёт и что попросил Глеб разбить?", {"c106"}),
    ("До какого числа нужно отправить чертёж веранды?", {"c107"}),
    ("Когда Синицын пришлёт расчёт утеплителя?", {"c108"}),
    ("Как быстро отвечает Зябликов на сообщения?", {"c109"}),
    ("Как в «Гнезде» принимают решения о закупках?", {"c110"}),
    ("Какой поставщик досок работает без предоплаты?", {"c201"}),
    ("Когда на Скворечнике начнётся отделка?", {"c202"}),
    ("С кем обсуждали сроки доставки кирпича?", {"c203"}),
    ("Что нужно согласовать с Чижовым до пятницы?", {"c204"}),
    ("Кто занижает сроки работ?", {"c205"}),
    ("В каком виде Глеб хочет получать сводки?", {"c206"}),
    ("Когда Иволгина сдаст отчёт по смете?", {"c301", "c302", "c303"}),
    ("На какое время перенесли созвон с Чижовым?", {"c402"}),
    ("В какие дни работает электрик?", {"c501"}),
    ("Как расплачиваться с электриком Снегирёвым?", {"c502"}),
    ("У кого ключи от дачи?", {"c902"}),
    ("Кто постоянно задерживает поставки и документы?", {"c601", "c602", "c603", "c604"}),
    ("До какого срока застрахован проект?", {"c952"}),
    ("Как обращаться к сметчику Иволгиной?", {"c951"}),
]


def mean_pairwise(vecs: dict, ids: list[str]) -> float:
    return float(np.mean([vecs[a] @ vecs[b] for a, b in itertools.combinations(ids, 2)]))


def bench(canaries: list[dict], model: str, dim: int) -> dict:
    t0 = time.time()
    emb = build(model, dim)
    load_s = time.time() - t0
    ids = [c["t"] for c in canaries]
    t1 = time.time()
    vs = emb.embed_passages([c["raw"] for c in canaries])
    embed_ms = (time.time() - t1) / len(ids) * 1000
    vecs = dict(zip(ids, vs))
    para = mean_pairwise(vecs, ["c301", "c302", "c303"])
    templ = mean_pairwise(vecs, ["c701", "c702", "c703"])
    person = mean_pairwise(vecs, ["c501", "c502", "c503"])
    contra = float(vecs["c401"] @ vecs["c402"])
    allp = [float(vecs[a] @ vecs[b]) for a, b in itertools.combinations(ids, 2)]
    mu, sd = float(np.mean(allp)), float(np.std(allp)) or 1.0
    z = lambda v: round((v - mu) / sd, 2)  # noqa: E731 — similarity in sigmas from the background of all pairs
    hits, rr, misses = 0, 0.0, []
    M = np.stack([vecs[i] for i in ids])
    for q, gold in QUERIES:
        sims = M @ emb.embed_query(q)
        order = [ids[i] for i in np.argsort(-sims)]
        rank = next((k for k, x in enumerate(order, 1) if x in gold), None)
        if rank and rank <= 10:
            hits += 1
        rr += 1.0 / rank if rank else 0.0
        if not rank or rank > 3:
            misses.append(f"{q} -> {sorted(gold)} at rank {rank}")
    return {"model": model, "dim": dim, "load_s": round(load_s, 1), "embed_ms": round(embed_ms, 1),
            "para": round(para, 3), "templ": round(templ, 3), "gap": round(para - templ, 3),
            "person": round(person, 3), "contra": round(contra, 3), "bg_mean": round(mu, 3),
            "para_z": z(para), "templ_z": z(templ), "person_z": z(person),
            "recall10": round(hits / len(QUERIES), 3), "mrr": round(rr / len(QUERIES), 3), "misses": misses}


def main() -> None:
    path, specs = sys.argv[1], sys.argv[2:]
    canaries = json.load(open(path, encoding="utf-8"))["canaries"]
    rows = []
    for spec in specs:
        model, dim = spec.rsplit(":", 1)
        try:
            rows.append(bench(canaries, model, int(dim)))
        except Exception as e:  # noqa: BLE001
            rows.append({"model": model, "error": f"{type(e).__name__}: {e}"})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    print("\n| Model | dim | background (all pairs) | paraphrases, σ | \"three estimates\", σ | facts about a person, σ | R@10 | MRR | ms/text |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "error" in r:
            print(f"| {r['model']} | error: {r['error'][:80]} |||||||||")
            continue
        print(f"| {r['model']} | {r['dim']} | {r['bg_mean']} | {r['para']} ({r['para_z']}) | {r['templ']} ({r['templ_z']}) | "
              f"{r['person']} ({r['person_z']}) | {r['recall10']} | {r['mrr']} | {r['embed_ms']} |")


if __name__ == "__main__":
    main()
