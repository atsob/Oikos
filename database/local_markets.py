"""Country profiles for Local Market Watch (Market Data -> Local Market Watch).

The country of usage is a setting (Tools -> App Settings -> Country of usage, stored as `homeCountry` in the 'app-settings' preference);
this module says what "the local market" means for each country: the benchmark index, the systemic banks, a set of large caps for
breadth (all Yahoo Finance symbols, checked to have two years of daily data), and the FRED series for the 10-year government yield
(the sovereign-spread leg compares it with Germany's, the euro-area benchmark — so only for euro countries: a profile with
"euro": False, such as the UK or the US, has no spread leg, because sterling and dollar yields sit structurally above the Bund).

`euro_from` is when the country joined the euro: the sovereign-spread rule is tested from then on only (before it the yield was in another currency and
the spread to the Bund was mostly the exchange-rate risk).

To add a country: add an entry below — nothing else changes.
"""
from __future__ import annotations

PROFILES: dict[str, dict] = {
    "GR": {"name": "Greece", "index": "GD.AT", "index_name": "ATHEX General Index", "fred": "GR", "euro_from": "2001-01-01",
           "bank_note": "about 40% of the index",
           "banks": {"ALPHA.AT": "Alpha Bank", "EUROB.AT": "Eurobank", "ETE.AT": "National Bank", "TPEIR.AT": "Piraeus"},
           "caps": ["ALPHA.AT", "EUROB.AT", "ETE.AT", "TPEIR.AT", "PPC.AT", "HTO.AT", "TITC.AT", "MOH.AT", "MTLN.AT", "BELA.AT", "EXAE.AT",
                    "ELPE.AT", "GEKTERNA.AT", "AEGN.AT", "CENER.AT", "LAMDA.AT", "ELHA.AT", "PPA.AT", "ADMIE.AT", "AKTR.AT"]},
    "FR": {"name": "France", "index": "^FCHI", "index_name": "CAC 40", "fred": "FR", "euro_from": "1999-01-01", "bank_note": "",
           "banks": {"BNP.PA": "BNP Paribas", "GLE.PA": "Société Générale", "ACA.PA": "Crédit Agricole"},
           "caps": ["MC.PA", "OR.PA", "TTE.PA", "SAN.PA", "AIR.PA", "SU.PA", "AI.PA", "RMS.PA", "EL.PA", "CS.PA", "BNP.PA", "DG.PA", "SAF.PA", "CAP.PA",
                    "KER.PA", "ORA.PA", "RI.PA", "DSY.PA", "ENGI.PA", "VIE.PA", "GLE.PA", "ACA.PA", "STMPA.PA", "HO.PA"]},
    "DE": {"name": "Germany", "index": "^GDAXI", "index_name": "DAX", "fred": None, "bank_note": "",
           "banks": {"DBK.DE": "Deutsche Bank", "CBK.DE": "Commerzbank"},
           "caps": ["SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "MBG.DE", "BMW.DE", "VOW3.DE", "BAS.DE", "BAYN.DE", "ADS.DE", "MUV2.DE", "DB1.DE", "IFX.DE",
                    "RWE.DE", "EOAN.DE", "HEN3.DE", "DHL.DE", "MRK.DE", "FRE.DE", "HEI.DE", "SHL.DE", "DBK.DE", "CBK.DE", "AIR.DE"]},
    "IT": {"name": "Italy", "index": "FTSEMIB.MI", "index_name": "FTSE MIB", "fred": "IT", "euro_from": "1999-01-01", "bank_note": "",
           "banks": {"ISP.MI": "Intesa Sanpaolo", "UCG.MI": "UniCredit", "BAMI.MI": "Banco BPM", "BPE.MI": "BPER"},
           "caps": ["ENI.MI", "ENEL.MI", "STLAM.MI", "RACE.MI", "G.MI", "PRY.MI", "TRN.MI", "SRG.MI", "MB.MI", "LDO.MI", "TEN.MI", "AMP.MI", "CPR.MI",
                    "BMED.MI", "MONC.MI", "REC.MI", "PST.MI", "A2A.MI", "DIA.MI", "IP.MI", "ISP.MI", "UCG.MI", "BAMI.MI", "BPE.MI"]},
    "ES": {"name": "Spain", "index": "^IBEX", "index_name": "IBEX 35", "fred": "ES", "euro_from": "1999-01-01", "bank_note": "",
           "banks": {"SAN.MC": "Santander", "BBVA.MC": "BBVA", "CABK.MC": "CaixaBank", "SAB.MC": "Sabadell", "BKT.MC": "Bankinter"},
           "caps": ["IBE.MC", "ITX.MC", "TEF.MC", "REP.MC", "AMS.MC", "FER.MC", "ELE.MC", "ENG.MC", "GRF.MC", "IAG.MC", "ACS.MC", "MAP.MC", "RED.MC",
                    "NTGY.MC", "CLNX.MC", "ANA.MC", "MRL.MC", "COL.MC", "SLR.MC", "SAN.MC", "BBVA.MC", "CABK.MC"]},
    "NL": {"name": "Netherlands", "index": "^AEX", "index_name": "AEX", "fred": "NL", "euro_from": "1999-01-01", "bank_note": "",
           "banks": {"INGA.AS": "ING", "ABN.AS": "ABN AMRO"},
           "caps": ["ASML.AS", "SHELL.AS", "PRX.AS", "UNA.AS", "HEIA.AS", "ADYEN.AS", "WKL.AS", "AD.AS", "PHIA.AS", "AKZA.AS", "NN.AS", "RAND.AS", "DSFIR.AS",
                    "IMCD.AS", "INGA.AS", "ABN.AS", "ASM.AS", "BESI.AS", "MT.AS"]},
    "PT": {"name": "Portugal", "index": "PSI20.LS", "index_name": "PSI", "fred": "PT", "euro_from": "1999-01-01", "bank_note": "",
           "banks": {"BCP.LS": "Millennium bcp"},
           "caps": ["EDP.LS", "GALP.LS", "JMT.LS", "SON.LS", "EDPR.LS", "NOS.LS", "NVG.LS", "ALTR.LS", "CTT.LS", "RENE.LS", "BCP.LS", "COR.LS", "SEM.LS"]},
    "GB": {"name": "United Kingdom", "index": "^FTSE", "index_name": "FTSE 100", "fred": "GB", "euro": False, "bank_note": "",
           "banks": {"LLOY.L": "Lloyds", "BARC.L": "Barclays", "NWG.L": "NatWest", "HSBA.L": "HSBC", "STAN.L": "Standard Chartered"},
           "caps": ["SHEL.L", "AZN.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L", "DGE.L", "BATS.L", "REL.L", "LSEG.L", "NG.L", "VOD.L", "CPG.L", "EXPN.L", "BA.L",
                    "RR.L", "TSCO.L", "GLEN.L", "AAL.L", "IMB.L", "LLOY.L", "BARC.L", "NWG.L", "HSBA.L"]},
    "US": {"name": "United States", "index": "^GSPC", "index_name": "S&P 500", "fred": "US", "euro": False, "bank_note": "",
           "banks": {"JPM": "JPMorgan", "BAC": "Bank of America", "WFC": "Wells Fargo", "C": "Citigroup", "GS": "Goldman Sachs", "MS": "Morgan Stanley"},
           "caps": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "LLY", "AVGO", "JPM", "TSLA", "V", "XOM", "UNH", "MA", "COST", "PG", "JNJ",
                    "HD", "WMT", "BAC", "ABBV", "KO", "CVX"]},
}


def home_country() -> str:
    """The country of usage chosen in Tools -> App Settings ('' when not set)."""
    from database.queries import _get_app_setting_str
    return _get_app_setting_str("homeCountry", "").strip().upper()


def get_profile(cc: str) -> dict | None:
    return PROFILES.get((cc or "").upper())


def supported() -> list[dict]:
    return [{"code": c, "name": p["name"]} for c, p in sorted(PROFILES.items(), key=lambda kv: kv[1]["name"])]
