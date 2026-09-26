# -*- coding: utf-8 -*-
"""반도체 소부장(소재/부품/장비/후공정) → RS 조인 → RS 내림차순 엑셀."""
import datetime as dt
import pandas as pd

# (code, category) — 반도체 소재/부품/장비/후공정
SOUBUJANG = [
    # 장비
    ("042700","장비"),("240810","장비"),("036930","장비"),("095610","장비"),
    ("084370","장비"),("319660","장비"),("031980","장비"),("281820","장비"),
    ("056190","장비"),("079370","장비"),("064290","장비"),("348210","장비"),
    ("140860","장비"),("322310","장비"),("098460","장비"),("089030","장비"),
    ("092870","장비"),("086390","장비"),("003160","장비"),("232140","장비"),
    ("036810","장비"),("238490","장비"),("403870","장비"),("083450","장비"),
    ("036200","장비"),("011930","장비"),("039440","장비"),("108230","장비"),
    ("144960","장비"),("417840","장비"),("265520","장비"),("083500","장비"),
    ("039030","장비"),("083310","장비"),("141000","장비"),("122640","장비"),
    ("160980","장비"),("071280","장비"),("089890","장비"),("068790","장비"),
    ("083930","장비"),("053610","장비"),("029460","장비"),("030530","장비"),
    ("110990","장비"),("109740","장비"),("212710","장비"),("299030","장비"),
    ("032940","장비"),("217500","장비"),
    # 소재
    ("014680","소재"),("357780","소재"),("036830","소재"),("005290","소재"),
    ("104830","소재"),("074600","소재"),("093370","소재"),("102710","소재"),
    ("166090","소재"),("241790","소재"),("220260","소재"),("077360","소재"),
    ("317330","소재"),("213420","소재"),("281740","소재"),("121600","소재"),
    ("092070","소재"),("445180","소재"),("482630","소재"),("112290","소재"),
    ("489500","소재"),("264660","소재"),("425040","소재"),("159010","소재"),
    ("311320","소재"),("402490","소재"),("171010","소재"),("089010","소재"),
    ("064760","소재"),("146320","소재"),("101160","소재"),("183300","소재"),
    ("114810","소재"),("357550","소재"),("417200","소재"),
    # 부품
    ("058470","부품"),("095340","부품"),("131290","부품"),("098120","부품"),
    ("007810","부품"),("353200","부품"),("222800","부품"),("036710","부품"),
    ("195870","부품"),("007660","부품"),("356860","부품"),("425420","부품"),
    ("200470","부품"),("080580","부품"),("253590","부품"),("252990","부품"),
    ("219130","부품"),("405100","부품"),("093520","부품"),("077500","부품"),
    ("254490","부품"),("031330","부품"),
    # 후공정(OSAT/Test)
    ("067310","후공정"),("036540","후공정"),("033640","후공정"),("330860","후공정"),
    ("061970","후공정"),("131970","후공정"),("204270","후공정"),("272110","후공정"),
    ("355150","후공정"),
    # 팹리스/설계/IP (참고 카테고리)
    ("108320","팹리스/IP"),("094360","팹리스/IP"),("394280","팹리스/IP"),
    ("399720","팹리스/IP"),("200710","팹리스/IP"),("445090","팹리스/IP"),
    ("452430","팹리스/IP"),("389020","팹리스/IP"),("123860","팹리스/IP"),
    ("054450","팹리스/IP"),("094170","팹리스/IP"),("032580","팹리스/IP"),
    ("102120","팹리스/IP"),("440110","팹리스/IP"),("490470","팹리스/IP"),
    ("432720","팹리스/IP"),
]

base = r"C:\Users\brad3\stock-screener\data"
# FDR 상장목록 캐시에서 빠지는 종목 폴백(전부 KOSDAQ)
FALLBACK = {
    "058470": "리노공업", "240810": "원익IPS", "036930": "주성엔지니어링",
    "095610": "테스", "056190": "에스에프에이", "140860": "파크시스템스",
    "403870": "HPSP", "357780": "솔브레인", "036830": "솔브레인홀딩스",
    "064760": "티씨케이", "222800": "심텍", "131970": "두산테스나",
}

uni = pd.read_parquet(base + r"\universe.parquet")
uni["code"] = uni["code"].astype(str).str.zfill(6)
name_by_code = dict(zip(uni["code"], uni["name"]))
for c, n in FALLBACK.items():
    name_by_code.setdefault(c, n)
mk_by_code   = dict(zip(uni["code"], uni["market"]))
for c in FALLBACK:
    mk_by_code.setdefault(c, "KOSDAQ")
close_by     = dict(zip(uni["code"], uni["close"]))
cap_by       = dict(zip(uni["code"], uni["marcap"]))

rs = pd.read_parquet(base + r"\rs_kr.parquet")
rs["code"] = rs["code"].astype(str).str.zfill(6)
rs_by = rs.set_index("code").to_dict("index")

rows, missing = [], []
seen = set()
for code, cat in SOUBUJANG:
    code = code.zfill(6)
    if code in seen:
        continue
    seen.add(code)
    if code not in name_by_code:
        missing.append((code, cat)); continue
    r = rs_by.get(code, {})
    rows.append({
        "종목명": name_by_code[code],
        "코드": code,
        "시장": mk_by_code.get(code, ""),
        "구분": cat,
        "RS": r.get("rs"),
        "RS_3M전": r.get("rs_3m"),
        "RS개선폭": r.get("rs_delta"),
        "3M수익%": r.get("ret_3m"),
        "6M수익%": r.get("ret_6m"),
        "12M수익%": r.get("ret_12m"),
        "MDD%": r.get("mdd"),
        "종가": (round(close_by.get(code)) if pd.notna(close_by.get(code)) else None),
        "시총(억)": (round(cap_by.get(code) / 1e8)
                   if pd.notna(cap_by.get(code)) and cap_by.get(code) else None),
    })

df = pd.DataFrame(rows)
df = df.sort_values("RS", ascending=False, na_position="last").reset_index(drop=True)
df.insert(0, "순위", range(1, len(df) + 1))

today = dt.date.today().isoformat()
out = base + rf"\반도체_소부장_RS_{today}.xlsx"
with pd.ExcelWriter(out, engine="xlsxwriter") as xw:
    df.to_excel(xw, sheet_name="RS순위", index=False)
    wb, ws = xw.book, xw.sheets["RS순위"]
    fmt_int = wb.add_format({"num_format": "#,##0"})
    fmt_pct = wb.add_format({"num_format": "0.0"})
    fmt_hdr = wb.add_format({"bold": True, "bg_color": "#1F4E78",
                             "font_color": "white", "border": 1,
                             "align": "center", "valign": "vcenter"})
    for c, col in enumerate(df.columns):
        ws.write(0, c, col, fmt_hdr)
    widths = {"순위":6,"종목명":16,"코드":8,"시장":8,"구분":9,"RS":6,
              "RS_3M전":9,"RS개선폭":9,"3M수익%":8,"6M수익%":8,"12M수익%":9,
              "MDD%":8,"종가":10,"시총(억)":12}
    for c, col in enumerate(df.columns):
        w = widths.get(col, 10)
        if col in ("종가","시총(억)"):
            ws.set_column(c, c, w, fmt_int)
        elif col in ("3M수익%","6M수익%","12M수익%","MDD%","RS개선폭"):
            ws.set_column(c, c, w, fmt_pct)
        else:
            ws.set_column(c, c, w)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, len(df), len(df.columns) - 1)
    # 조건부 서식: RS 높을수록 진하게
    ws.conditional_format(1, 5, len(df), 5,
        {"type": "3_color_scale", "min_color": "#F8696B",
         "mid_color": "#FFEB84", "max_color": "#63BE7B"})

print("총 종목:", len(df))
print("유니버스에 없는 코드:", missing)
print("RS 상위 15:")
print(df[["순위","종목명","구분","RS","3M수익%","12M수익%"]].head(15).to_string(index=False))
print("\n엑셀 저장:", out)
