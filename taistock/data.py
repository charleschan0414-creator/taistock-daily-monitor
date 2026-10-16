from datetime import datetime
from .models import Quote


def _number(value):
    text = str(value or "").replace(",", "").strip()
    if text in ("", "--", "---"):
        return 0
    return float(text)


def _date(value):
    text = str(value).replace("/", "").replace("-", "")
    if len(text) == 7:  # 民國年
        text = str(int(text[:3]) + 1911) + text[3:]
    return datetime.strptime(text[:8], "%Y%m%d").strftime("%Y-%m-%d")


def parse_twse_quotes(data):
    return [Quote(str(x["Code"]), x.get("Name", ""), _number(x.get("ClosingPrice")),
                  int(_number(x.get("TradeVolume"))), _date(x.get("Date")), "上市") for x in data]


def parse_tpex_quotes(data):
    return [Quote(str(x.get("SecuritiesCompanyCode", x.get("Code"))), x.get("CompanyName", x.get("Name", "")),
                  _number(x.get("Close")), int(_number(x.get("TradingShares"))),
                  _date(x.get("Date")), "上櫃") for x in data]


def ensure_same_date(quote_date, institution_date, market):
    if quote_date != institution_date:
        raise ValueError(f"{market}行情與法人日期不一致：{quote_date} / {institution_date}")
    return quote_date
