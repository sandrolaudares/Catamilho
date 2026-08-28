"""Classificador fenologico por regras explicaveis — milho safrinha, Medio Norte MT.

Assinatura diagnostico (NDVI, Sentinel-2) da dupla safra soja -> milho:
  set-out  entressafra / preparo ......... 0.25-0.35
  nov-jan  pico da soja .................. >= 0.70
  jan-fev  VALE: colheita soja + plantio   queda >= 0.15 e <= 0.55
  mar-mai  PICO do milho safrinha ........ >= 0.70
  jun-jul  senescencia / colheita ........ queda >= 0.25 do pico

O "W invertido" (pico -> vale -> pico) e quase exclusivo da dupla safra.
Mesma filosofia do bauxita-sam: regras auditaveis, confianca por pesos.
"""
import datetime as dt

import numpy as np

PESO_MIN = 5  # peso minimo disponivel para classificar com seguranca


def _mensal(series):
    acc = {m: [] for m in range(1, 13)}
    for s in series:
        try:
            d = dt.date.fromisoformat(str(s["date"])[:10])
            acc[d.month].append(float(s["ndvi"]))
        except Exception:
            continue
    return {m: (float(np.mean(v)) if v else None) for m, v in acc.items()}


def _avg(nd, *ms):
    vals = [nd[m] for m in ms if nd[m] is not None]
    return float(np.mean(vals)) if vals else None


def _max(nd, *ms):
    vals = [nd[m] for m in ms if nd[m] is not None]
    return max(vals) if vals else None


def _min(nd, *ms):
    vals = [nd[m] for m in ms if nd[m] is not None]
    return min(vals) if vals else None


def _estagio(fase, janela, esperado, obs, lo=None, hi=None):
    ok = None
    if obs is not None:
        if lo is not None and hi is not None:
            ok = lo <= obs <= hi
        elif lo is not None:
            ok = obs >= lo
        elif hi is not None:
            ok = obs <= hi
    return {"fase": fase, "janela": janela, "esperado": esperado,
            "observado": round(obs, 2) if obs is not None else None, "ok": ok}


def _insuficiente(nd, regras=None):
    return {
        "classe": "dados_insuficientes",
        "veredito": "Dados insuficientes — amplie o período ou o limite de nuvens",
        "emoji": "⚠️", "confianca": 0.0,
        "regras": regras or [], "estagios": [],
        "ndvi_mensal": {str(m): nd[m] for m in range(1, 13)},
        "resumo": "Menos de 4 meses com observações válidas.",
        "ciclo_em_andamento": False,
    }


def classificar(series, fim_serie=None):
    nd = _mensal(series)
    obs = [v for v in nd.values() if v is not None]
    if len(obs) < 4:
        return _insuficiente(nd)

    pico_soja = _max(nd, 11, 12, 1)
    vale = _min(nd, 1, 2)
    pico_milho = _max(nd, 3, 4, 5)
    pos_pico = _max(nd, 6, 7, 8)
    fim = _avg(nd, 6, 7)
    amplitude = max(obs) - min(obs)

    regras = []

    def add(nome, desc, peso, disponivel, atendida):
        regras.append({
            "nome": nome, "descricao": desc, "peso": peso,
            "disponivel": bool(disponivel),
            "atendida": (bool(atendida) if disponivel else None),
        })

    add("vigor_milho",
        "Pico de NDVI em mar–mai ≥ 0.70 (vigor do milho safrinha)", 3,
        pico_milho is not None,
        pico_milho is not None and pico_milho >= 0.70)
    add("pico_no_outono",
        "Pico de mar–mai supera jun–ago em ≥ 0.15 (ciclo curto de outono)", 1,
        pico_milho is not None and pos_pico is not None,
        pico_milho is not None and pos_pico is not None
        and (pico_milho - pos_pico) >= 0.15)
    add("vale_jan_fev",
        "Vale jan–fev (colheita da soja + plantio do milho): queda ≥ 0.15 e NDVI ≤ 0.55", 2,
        vale is not None and pico_soja is not None,
        vale is not None and pico_soja is not None
        and (pico_soja - vale) >= 0.15 and vale <= 0.55)
    add("soja_forte",
        "Soja vigorosa antes do vale (nov–jan ≥ 0.65)", 1,
        pico_soja is not None,
        pico_soja is not None and pico_soja >= 0.65)
    add("senescencia",
        "Senescência/colheita do milho em jun–jul (queda ≥ 0.25 do pico)", 2,
        fim is not None and pico_milho is not None,
        fim is not None and pico_milho is not None
        and (pico_milho - fim) >= 0.25)
    add("amplitude",
        "Amplitude anual ≥ 0.35 (rotação anual, não cobertura perene)", 1,
        True, amplitude >= 0.35)

    peso_disp = sum(r["peso"] for r in regras if r["disponivel"])
    peso_ok = sum(r["peso"] for r in regras if r["disponivel"] and r["atendida"])
    if peso_disp < PESO_MIN:
        return _insuficiente(nd, regras)
    conf = round(peso_ok / peso_disp, 3)

    if conf >= 0.72:
        classe, veredito, emoji = "milho_safrinha", \
            "Milho safrinha — dupla safra soja + milho", "🌽"
    elif conf >= 0.5:
        classe, veredito, emoji = "provavel_safrinha", \
            "Provável milho safrinha (sinais parciais)", "🌽"
    else:
        pico_verao = _max(nd, 12, 1, 2)
        if pico_soja and (pico_milho is None or pico_milho < 0.6) and pico_soja >= 0.7:
            classe, veredito, emoji = "soja_unica", \
                "Soja em safra única — sem sinal de milho safrinha", "🌱"
        elif amplitude < 0.25:
            classe, veredito, emoji = "perene", \
                "Cobertura perene/pastagem — sem assinatura de dupla safra", "🟩"
        elif pico_verao and pico_verao >= 0.7:
            classe, veredito, emoji = "pico_verao", \
                "Pico no verão — milho 1ª safra ou soja (regras não separam)", "🌾"
        else:
            classe, veredito, emoji = "inconclusivo", \
                "Inconclusivo — padrão fora das curvas de referência", "❓"

    # ciclo ainda em andamento? (ultima data recente e antes da colheita)
    datas = sorted(str(s["date"])[:10] for s in series)
    ciclo_andamento = False
    if datas:
        try:
            last = dt.date.fromisoformat(datas[-1])
            ciclo_andamento = (dt.date.today() - last).days < 45 and last.month <= 7
        except Exception:
            pass

    estagios = [
        _estagio("Entressafra / preparo de solo", "set–out",
                 "0.25–0.35", _avg(nd, 9, 10), 0.20, 0.45),
        _estagio("Pico da soja", "nov–jan", "≥ 0.65", pico_soja, lo=0.65),
        _estagio("Vale: colheita soja + plantio milho", "jan–fev",
                 "≤ 0.55", vale, hi=0.55),
        _estagio("Pico do milho safrinha", "mar–mai", "≥ 0.70",
                 pico_milho, lo=0.70),
        _estagio("Senescência / colheita do milho", "jun–jul",
                 "queda ≥ 0.25", fim,
                 hi=(pico_milho - 0.25) if pico_milho is not None else None),
    ]

    def f(v):
        return f"{v:.2f}" if v is not None else "—"

    resumo = (
        f"Pico soja (nov–jan): {f(pico_soja)} · Vale (jan–fev): {f(vale)} · "
        f"Pico milho (mar–mai): {f(pico_milho)} · Jun–jul: {f(fim)} · "
        f"Amplitude: {amplitude:.2f}."
    )
    if ciclo_andamento:
        resumo += " Ciclo em andamento — regras de senescência podem estar incompletas."

    return {
        "classe": classe, "veredito": veredito, "emoji": emoji,
        "confianca": conf, "regras": regras, "estagios": estagios,
        "ndvi_mensal": {str(m): (round(nd[m], 3) if nd[m] is not None else None)
                        for m in range(1, 13)},
        "resumo": resumo, "ciclo_em_andamento": ciclo_andamento,
    }
