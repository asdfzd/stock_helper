"""Conservative classification from public usa10099 fields, never ticker suffixes."""
import re


def classify_symbol(fields):
    evidence = {k: fields[k] for k in ("mkgb", "upgb", "isEtf", "stk_nm", "stk_enm") if k in fields}
    korean = str(fields.get("stk_nm", ""))
    english = str(fields.get("stk_enm", "")).upper()
    # The actual four warrant samples consistently have BOTH explicit descriptors.
    # These are official product names, not a claimed warrant enum.
    if "워런트" in korean and re.search(r"\bC/WTS\b|\bWARRANTS?\b", english):
        kind, rule = "warrant", "official_names_explicit_warrant"
    elif "유닛" in korean and re.search(r"\bUNITS?\b", english):
        kind, rule = "other", "official_names_explicit_unit"
    elif re.search(r"\bETN\b", english) and "ETN" in korean:
        kind, rule = "ETN", "official_names_explicit_etn"
    elif fields.get("isEtf") == "Y":
        kind, rule = "ETF", "usa10099_isEtf_Y"
    elif fields.get("isEtf") == "N" and not re.search(r"\bWARRANTS?\b|\bC/WTS\b|\bUNITS?\b|\bPREFERRED\b|\bRIGHTS?\b", english) and re.search(r"\bCOMMON STOCK\b|\bORDINARY SHARES\b", english):
        kind, rule = "common", "official_name_explicit_common"
    else:
        kind, rule = "unknown", "no_confirmed_product_type"
    return kind, {"source": "kiwoom:usa10099", "rule": rule, "fields": evidence}


def excluded_type(kind, policy):
    return policy == "common_and_unknown" and kind not in {"common", "unknown", "type_unknown"}
