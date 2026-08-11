from datetime import datetime, date
import pandas as pd
import numpy as np
import pandas_ta as ta
import streamlit as st
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
import warnings

# Configure page layout to wide mode
st.set_page_config(page_title="Stock Watchlist Dashboard", layout="wide")

# --- CUSTOM CSS TO OPTIMIZE SPACE ---
st.markdown("""
    <style>
        .block-container {
            padding-top: 3rem !important;
            padding-bottom: 0rem !important;
        }
        div[data-testid="stTabs"] {
            margin-bottom: -1rem;
        }
        th {
            text-align: center !important;
        }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# DATABASE CONNECTION (GOOGLE SHEETS)
# ---------------------------------------------------------
@st.cache_resource
def get_google_sheets():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    credentials = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )
    gc = gspread.authorize(credentials)
    
    wl_sheet = gc.open("Watchlist Database").sheet1
    try:
        pt_sheet = gc.open("Watchlist Database").worksheet("Portfolio")
    except gspread.exceptions.WorksheetNotFound:
        pt_sheet = gc.open("Watchlist Database").add_worksheet(title="Portfolio", rows="1000", cols="20")
    
    if not wl_sheet.get_all_values():
        wl_sheet.append_row(["ticker", "name", "date_added", "entry_price"])
        
    if not pt_sheet.get_all_values():
        pt_sheet.append_row(["ticker", "entry_price", "qty", "entry_date", "exit_price", "exit_date", "status"])
        
    return wl_sheet, pt_sheet

try:
    db_sheet, port_sheet = get_google_sheets()
except Exception as e:
    st.error(f"Failed to connect to Google Sheets. Verify your secrets.toml and share settings. Error: {e}")
    st.stop()

# ---------------------------------------------------------
# HELPER FOR SAFE COLUMN EXTRACTION
# ---------------------------------------------------------
def get_safe_col(df, prefix, exclude_prefixes=[]):
    matches = [c for c in df.columns if c.startswith(prefix) and not any(c.startswith(ep) for ep in exclude_prefixes)]
    return matches[0] if matches else None

# ---------------------------------------------------------
# GLOBAL STYLES FOR BOLD HEADERS & UI
# ---------------------------------------------------------
bold_header_styles = [
    {'selector': 'th', 'props': [('font-weight', 'bold !important'), ('font-size', '14px')]},
    {'selector': 'th.col_heading', 'props': [('font-weight', 'bold !important')]},
    {'selector': 'th.col_heading.level0', 'props': [('font-weight', 'bold !important'), ('font-size', '15px')]},
    {'selector': 'th.col_heading.level1', 'props': [('font-weight', 'bold !important')]}
]

def color_trend_cells(val):
    if val == "Bullish":
        return "background-color: #E8F5E9; color: #1B5E20; font-size: 11px; font-weight: bold; text-align: center;"
    elif val == "Bearish":
        return "background-color: #FFEBEE; color: #B71C1C; font-size: 11px; font-weight: bold; text-align: center;"
    elif val == "-":
        return "background-color: transparent; color: #888; text-align: center;"
    elif val == "":
        return "background-color: transparent;" 
    return "text-align: center;"

def apply_styles(df):
    sub_cols = [
        ("Daily", "EMA"), ("Daily", "MACD"), ("Daily", "ST"), ("Daily", "RSI"),
        (" ", " "),
        ("Weekly", "EMA"), ("Weekly", "MACD"), ("Weekly", "ST"), ("Weekly", "RSI"),
        ("  ", "  "),
        ("Monthly", "EMA"), ("Monthly", "MACD"), ("Monthly", "ST"), ("Monthly", "RSI")
    ]
    styled = df.style.map(color_trend_cells, subset=sub_cols)
    styled = styled.set_properties(subset=[("Price ($/₹)", "")], **{'text-align': 'center'})
    styled = styled.format({("Price ($/₹)", ""): "{:.2f}"}) 
    styled = styled.set_table_styles(bold_header_styles)
    return styled

def apply_sell_styles(df):
    sub_cols = [
        ("Daily", "EMA"), ("Daily", "MACD"), ("Daily", "ST"), ("Daily", "RSI"),
        (" ", " "),
        ("Weekly", "EMA"), ("Weekly", "MACD"), ("Weekly", "ST"), ("Weekly", "RSI"),
        ("  ", "  "),
        ("Monthly", "EMA"), ("Monthly", "MACD"), ("Monthly", "ST"), ("Monthly", "RSI")
    ]
    styled = df.style.map(color_trend_cells, subset=sub_cols)
    
    def style_overall_pct(val):
        if isinstance(val, (int, float)):
            if val < 0:
                return 'color: #D32F2F; font-weight: bold; text-align: center;'
            elif val > 0:
                return 'color: #1B5E20; font-weight: bold; text-align: center;'
        return 'text-align: center;'

    styled = styled.map(style_overall_pct, subset=[("Overall %", "")])
    styled = styled.set_properties(subset=[("Price ($/₹)", ""), ("Bought Price", "")], **{'text-align': 'center'})
    styled = styled.format({("Price ($/₹)", ""): "{:.2f}", ("Bought Price", ""): "{:.2f}", ("Overall %", ""): "{:.2f}%"}) 
    styled = styled.set_table_styles(bold_header_styles)
    return styled

# ---------------------------------------------------------
# LOAD SESSION STATES
# ---------------------------------------------------------
if "watchlist_data" not in st.session_state:
    raw_rows = db_sheet.get_all_values()
    cleaned_records = []
    if len(raw_rows) > 1:
        headers = [str(h).strip().lower() for h in raw_rows[0]]
        t_idx = headers.index("ticker") if "ticker" in headers else 0
        n_idx = headers.index("name") if "name" in headers else 1
        d_idx = headers.index("date_added") if "date_added" in headers else 2
        p_idx = headers.index("entry_price") if "entry_price" in headers else 3
        
        for row in raw_rows[1:]:
            if len(row) > t_idx and str(row[t_idx]).strip() != "":
                t_val = str(row[t_idx]).strip()
                n_val = str(row[n_idx]).strip() if len(row) > n_idx and str(row[n_idx]).strip() != "" else t_val
                d_val = str(row[d_idx]).strip() if d_idx < len(row) and str(row[d_idx]).strip() != "" else datetime.today().strftime("%Y-%m-%d")
                try:
                    p_val = float(row[p_idx]) if len(row) > p_idx and str(row[p_idx]).strip() != "" else 0.0
                except ValueError:
                    p_val = 0.0
                    
                cleaned_records.append({"ticker": t_val, "name": n_val, "date_added": d_val, "entry_price": p_val})
    st.session_state.watchlist_data = cleaned_records

if "portfolio_data" not in st.session_state:
    raw_port = port_sheet.get_all_values()
    port_records = []
    if len(raw_port) > 1:
        headers = [str(h).strip().lower() for h in raw_port[0]]
        for i, row in enumerate(raw_port[1:]):
            if not row or not str(row[0]).strip(): continue
            row = row + [""] * (7 - len(row))
            
            try: ep = float(row[1]) if str(row[1]).strip() else 0.0
            except: ep = 0.0
            try: qty = float(row[2]) if str(row[2]).strip() else 0.0
            except: qty = 0.0
            try: exp = float(row[4]) if str(row[4]).strip() else 0.0
            except: exp = 0.0
            
            port_records.append({
                "row_idx": i + 2, 
                "ticker": str(row[0]).strip(),
                "entry_price": ep,
                "qty": qty,
                "entry_date": str(row[3]).strip(),
                "exit_price": exp,
                "exit_date": str(row[5]).strip(),
                "status": str(row[6]).strip() if str(row[6]).strip() else "Live"
            })
    st.session_state.portfolio_data = port_records

# ---------------------------------------------------------
# 1. TOP NAVIGATION (TABS)
# ---------------------------------------------------------
tab_watchlist, tab_portfolio, tab_sell, tab_buy, tab_strong_up, tab_uptrend, tab_downtrend = st.tabs(
    ["📋 Watchlist", "💼 Portfolio", "🔴 Sell Stock", "🟢 Buy Signal", "🔥 Strong Uptrend", "📈 Uptrend Stocks", "📉 Downtrend Stocks"]
)

# ---------------------------------------------------------
# 2. WATCHLIST TAB
# ---------------------------------------------------------
with tab_watchlist:
    st.write("") 
    
    with st.expander("➕ Manage Watchlist (Add / Remove Stocks)", expanded=False):
        col1, col2, col3, col4, col5, col6 = st.columns([1.2, 0.9, 1.2, 0.8, 1.2, 0.8])
        
        with col1:
            timeframe = st.radio("Select Timeframe:", ["Daily", "Weekly", "Monthly"], horizontal=True, key="tf_watchlist")
        with col2:
            market_type = st.selectbox("Market:", ["NSE (India)", "US (Global)"], label_visibility="visible")
        with col3:
            new_symbol = st.text_input("Add Ticker (e.g. SBIN / AAPL):", "").upper()
        with col4:
            st.write(" ") 
            st.write(" ")
            if st.button("➕ Add", key="add_wl"):
                if new_symbol:
                    with st.spinner("Adding..."):
                        formatted_symbol = new_symbol if new_symbol.endswith((".NS", ".BO")) or "US" in market_type else f"{new_symbol}.NS"
                        existing_tickers = [item["ticker"] for item in st.session_state.watchlist_data]
                        
                        if formatted_symbol not in existing_tickers:
                            try:
                                new_stock = yf.Ticker(formatted_symbol)
                                hist_1d = new_stock.history(period="5d").dropna(subset=["Close", "High", "Low"])
                                if not hist_1d.empty:
                                    current_price = round(hist_1d["Close"].iloc[-1], 2)
                                    company_name = new_stock.info.get("shortName", formatted_symbol)
                                    date_added_str = datetime.today().strftime("%Y-%m-%d")
                                    
                                    db_sheet.append_row([formatted_symbol, company_name, date_added_str, current_price])
                                    st.session_state.watchlist_data.append({"ticker": formatted_symbol, "name": company_name, "date_added": date_added_str, "entry_price": current_price})
                                    st.success(f"Added {company_name}!")
                                    st.rerun()
                                else:
                                    st.error("Could not fetch data.")
                            except Exception as e: st.error(f"Error: {e}")
                        else: st.warning("Already in watchlist!")

        with col5:
            current_stocks = {item["ticker"]: item["name"] for item in st.session_state.watchlist_data if "ticker" in item}
            delete_symbol = st.selectbox("Remove Stock:", options=list(current_stocks.keys()), index=None, placeholder="Search to remove...", format_func=lambda x: f"{current_stocks[x]} ({x})")

        with col6:
            st.write(" ") 
            st.write(" ")
            if st.button("❌ Remove", key="rm_wl"):
                if delete_symbol:
                    with st.spinner("Removing..."):
                        try:
                            cell = db_sheet.find(delete_symbol)
                            if cell: db_sheet.delete_rows(cell.row)
                            st.session_state.watchlist_data = [item for item in st.session_state.watchlist_data if item.get("ticker") != delete_symbol]
                            st.rerun()
                        except Exception as e: st.error(f"Failed: {e}")

    # Fallback for timeframe if expander is closed
    if "tf_watchlist" not in st.session_state:
        st.session_state.tf_watchlist = "Daily"
    timeframe = st.session_state.tf_watchlist

    if st.button("🔄 Sync with Google Sheets"):
        del st.session_state.watchlist_data
        del st.session_state.portfolio_data
        st.rerun()

    def fetch_watchlist_table(items, tf):
        rows = []
        if not items: return pd.DataFrame()
        yf_period, yf_interval = ("6mo", "1d") if tf == "Daily" else (("2y", "1wk") if tf == "Weekly" else ("5y", "1mo"))

        for item in items:
            ticker = item.get("ticker")
            name = item.get("name", ticker)
            date_added_str = str(item.get("date_added", datetime.today().strftime("%Y-%m-%d")))
            try: entry_price = float(item.get("entry_price", 0))
            except ValueError: entry_price = 0.0

            if not ticker: continue

            current_price = entry_price
            ema_5 = ema_8 = ema_13 = ema_21 = macd_val = signal_val = rsi_val = 0.0
            st_upper_display = st_lower_display = "-"
            performance_str = "(Data Missing)"
            days_since = 0

            try: days_since = (datetime.today() - datetime.strptime(date_added_str, "%Y-%m-%d")).days
            except ValueError: pass

            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period=yf_period, interval=yf_interval).dropna(subset=["Close", "High", "Low"])

                if not hist.empty:
                    current_price = round(hist["Close"].iloc[-1], 2)
                    if entry_price > 0:
                        pct_change = ((current_price - entry_price) / entry_price) * 100
                        direction = "Up" if pct_change >= 0 else "Down"
                        performance_str = f"({abs(pct_change):.1f}% {direction} - {days_since} days)"
                    else:
                        performance_str = "(0.0% - 0 days)"

                    if len(hist) > 30:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            hist.ta.ema(length=5, append=True)
                            hist.ta.ema(length=8, append=True)
                            hist.ta.ema(length=13, append=True)
                            hist.ta.ema(length=21, append=True)
                            hist.ta.macd(fast=8, slow=21, signal=9, append=True)
                            hist.ta.supertrend(length=10, multiplier=3, append=True)
                            hist.ta.rsi(length=14, append=True)

                        latest = hist.iloc[-1]
                        superts_col = get_safe_col(hist, "SUPERTs_")
                        supertl_col = get_safe_col(hist, "SUPERTl_")
                        macd_col = get_safe_col(hist, "MACD_", ["MACDs_", "MACDh_"])
                        macds_col = get_safe_col(hist, "MACDs_")

                        st_upper_display = "-" if not superts_col or pd.isna(latest[superts_col]) else f"{latest[superts_col]:.2f}"
                        st_lower_display = "-" if not supertl_col or pd.isna(latest[supertl_col]) else f"{latest[supertl_col]:.2f}"
                        ema_5 = latest.get("EMA_5", 0)
                        ema_8 = latest.get("EMA_8", 0)
                        ema_13 = latest.get("EMA_13", 0)
                        ema_21 = latest.get("EMA_21", 0)
                        macd_val = latest[macd_col] if macd_col else 0
                        signal_val = latest[macds_col] if macds_col else 0
                        rsi_val = latest.get("RSI_14", 0)
                    else: performance_str = f"{performance_str} (Data < 30 periods)"
                else: performance_str = "(Data Empty)"
            except Exception: performance_str = "(API Data Error)"

            rows.append({
                "Stock": name, "Price ($/₹)": current_price, "Since Watchlisted": performance_str,
                "5 EMA": ema_5, "8 EMA": ema_8, "13 EMA": ema_13, "21 EMA": ema_21,
                "MACD": macd_val, "SIGNAL": signal_val, "ST Upper Ba": st_upper_display, "ST Lower Bar": st_lower_display, "RSI": rsi_val,
            })
        return pd.DataFrame(rows)

    def color_performance(val):
        if "Up" in str(val): return "color: #1B5E20; font-weight: bold; text-align: center;"  
        elif "Down" in str(val): return "color: #D32F2F; font-weight: bold; text-align: center;"  
        return "text-align: center;"

    df_watchlist = fetch_watchlist_table(st.session_state.watchlist_data, timeframe)
    if not df_watchlist.empty:
        styled_df = df_watchlist.style.map(color_performance, subset=["Since Watchlisted"])
        cols_to_center = [col for col in df_watchlist.columns if col != "Stock"]
        styled_df = styled_df.set_properties(subset=cols_to_center, **{'text-align': 'center'})
        styled_df = styled_df.format({"Price ($/₹)": "{:.2f}"})

        st.markdown("---")
        st.dataframe(
            styled_df, hide_index=True, use_container_width=True,
            column_config={
                "Stock": st.column_config.TextColumn(width=150),
                "Price ($/₹)": st.column_config.NumberColumn(format="%.2f", width=80),
                "Since Watchlisted": st.column_config.TextColumn(width=200),
                "5 EMA": st.column_config.NumberColumn(format="%.2f", width=70),
                "8 EMA": st.column_config.NumberColumn(format="%.2f", width=70),
                "13 EMA": st.column_config.NumberColumn(format="%.2f", width=70),
                "21 EMA": st.column_config.NumberColumn(format="%.2f", width=70),
                "MACD": st.column_config.NumberColumn(format="%.2f", width=60),
                "SIGNAL": st.column_config.NumberColumn(format="%.2f", width=60),
                "ST Upper Ba": st.column_config.TextColumn(width=80),
                "ST Lower Bar": st.column_config.TextColumn(width=80),
                "RSI": st.column_config.NumberColumn(format="%.2f", width=60),
            }
        )
    else:
        st.markdown("---")
        st.info("Your watchlist is completely empty. Add some stocks above!")

# ---------------------------------------------------------
# 3. PORTFOLIO TAB IMPLEMENTATION
# ---------------------------------------------------------
with tab_portfolio:
    st.write("")
    
    with st.expander("➕ Manage Portfolio (Add / Remove Trades)", expanded=False):
        col1, col2, col3, col4, col5 = st.columns([1, 1, 1, 1, 1])
        
        with col1: p_market = st.selectbox("Market", ["NSE", "US"], key="p_market")
        with col2: p_ticker = st.text_input("Ticker", key="p_ticker").upper()
        with col3: p_price = st.number_input("Entry Price", min_value=0.0, format="%.2f", step=1.0)
        with col4: p_qty = st.number_input("Quantity", min_value=0.0, format="%.2f", step=1.0)
        with col5: p_date = st.date_input("Entry Date", value=date.today())

        if st.button("➕ Log Trade"):
            if p_ticker and p_price > 0 and p_qty > 0:
                formatted_ticker = p_ticker if p_ticker.endswith(".NS") or p_market == "US" else f"{p_ticker}.NS"
                
                existing_live_record = None
                for p in st.session_state.portfolio_data:
                    if p["ticker"] == formatted_ticker and p["status"] == "Live":
                        existing_live_record = p
                        break
                
                with st.spinner("Logging to Ledger..."):
                    if existing_live_record:
                        old_qty = existing_live_record["qty"]
                        old_price = existing_live_record["entry_price"]
                        new_total_qty = old_qty + p_qty
                        new_avg_price = ((old_qty * old_price) + (p_qty * p_price)) / new_total_qty
                        
                        gs_row = existing_live_record["row_idx"]
                        port_sheet.update_cell(gs_row, 2, new_avg_price)
                        port_sheet.update_cell(gs_row, 3, new_total_qty)
                        st.success(f"Added to existing {formatted_ticker} position. New Avg Price: {new_avg_price:.2f}")
                    else:
                        port_sheet.append_row([
                            formatted_ticker, p_price, p_qty, p_date.strftime("%Y-%m-%d"), "", "", "Live"
                        ])
                        st.success(f"Logged new trade for {formatted_ticker}!")
                    
                    del st.session_state.portfolio_data
                    st.rerun()
            else:
                st.error("Please fill in Ticker, valid Price, and valid Quantity.")
                
        st.markdown("---")
        
        col_del1, col_del2 = st.columns([2, 1])
        with col_del1:
            port_tickers = {p["ticker"]: p["ticker"] for p in st.session_state.portfolio_data}
            p_delete_ticker = st.selectbox("Remove Stock completely from Portfolio:", options=list(port_tickers.keys()), index=None, placeholder="Search to delete...")
        with col_del2:
            st.write(" ")
            st.write(" ")
            if st.button("❌ Delete Trade"):
                if p_delete_ticker:
                    with st.spinner("Deleting..."):
                        record_to_del = next((p for p in st.session_state.portfolio_data if p["ticker"] == p_delete_ticker), None)
                        if record_to_del:
                            port_sheet.delete_rows(record_to_del["row_idx"])
                            del st.session_state.portfolio_data
                            st.success(f"Deleted {p_delete_ticker} from Portfolio. It will now return to scanners.")
                            st.rerun()

    port_rows = []
    for item in st.session_state.portfolio_data:
        ticker = item["ticker"]
        name = next((w["name"] for w in st.session_state.watchlist_data if w["ticker"] == ticker), ticker)
        
        entry_price = item["entry_price"]
        actual_qty = item["qty"]
        invested = entry_price * actual_qty
        
        current_price = 0.0
        today_change_pct = 0.0
        
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d").dropna(subset=["Close", "High", "Low"])
            if not hist.empty:
                current_price = round(hist["Close"].iloc[-1], 2)
                if len(hist) > 1:
                    prev_close = hist["Close"].iloc[-2]
                    today_change_pct = ((current_price - prev_close) / prev_close) * 100
        except: pass
        
        # Standardized 1 Lakh Capital Calculation
        std_capital = 100000.0
        std_qty = round(std_capital / entry_price) if entry_price > 0 else 0
        std_invested = entry_price * std_qty
        
        if item["status"] == "Live":
            current_value = current_price * actual_qty
            std_current_value = current_price * std_qty
            days_in = max(1, (datetime.today() - datetime.strptime(item["entry_date"], "%Y-%m-%d")).days)
        else:
            current_value = item["exit_price"] * actual_qty
            std_current_value = item["exit_price"] * std_qty
            try: days_in = max(1, (datetime.strptime(item["exit_date"], "%Y-%m-%d") - datetime.strptime(item["entry_date"], "%Y-%m-%d")).days)
            except: days_in = 1
            
        actual_pnl = current_value - invested
        total_pnl = std_current_value - std_invested
        
        overall_change = (actual_pnl / invested) * 100 if invested > 0 else 0
        
        port_rows.append({
            "_row_idx": item["row_idx"],
            "Stock": name,
            "Price": current_price,
            "Today\nChange %": today_change_pct,
            "Overall\nChange %": overall_change,
            "Total PNL": total_pnl,
            "PNL": actual_pnl,
            "Days\nIn": days_in,
            "Invested\nAmount": invested,
            "Current\nValue": current_value,
            "Entry\nPrice": entry_price,
            "Qty": actual_qty,
            "Entry\nDate": item["entry_date"],
            "Exit\nPrice": item["exit_price"],
            "Exit\nDate": item["exit_date"],
            "Status": item["status"]
        })

    if port_rows:
        df_port = pd.DataFrame(port_rows)
        df_port["_sort"] = df_port["Status"].apply(lambda x: 0 if x == "Live" else 1)
        df_port = df_port.sort_values(by=["_sort", "Invested\nAmount"], ascending=[True, False]).drop(columns=["_sort"]).reset_index(drop=True)
        
        def style_portfolio(df):
            def current_val_color(row):
                colors = [''] * len(row)
                cv_idx = row.index.get_loc("Current\nValue")
                if row["Current\nValue"] < row["Invested\nAmount"]:
                    colors[cv_idx] = 'color: #FF5252; font-weight: bold;'
                elif row["Current\nValue"] > row["Invested\nAmount"]:
                    colors[cv_idx] = 'color: #1B5E20; font-weight: bold;'
                return colors

            def metric_color(val):
                if isinstance(val, (float, int, np.floating, np.integer)):
                    if val > 0: return 'color: #1B5E20; font-weight: bold;'
                    elif val < 0: return 'color: #FF5252; font-weight: bold;'
                return ''

            s = df.style.apply(current_val_color, axis=1)
            s = s.map(metric_color, subset=["Total PNL", "PNL", "Today\nChange %", "Overall\nChange %"])
            return s
        
        edited_df = st.data_editor(
            style_portfolio(df_port),
            use_container_width=True,
            hide_index=True,
            column_order=["Stock", "Price", "Today\nChange %", "Overall\nChange %", "Total PNL", "PNL", "Days\nIn", "Invested\nAmount", "Current\nValue", "Entry\nPrice", "Qty", "Entry\nDate", "Exit\nPrice", "Exit\nDate", "Status"],
            disabled=["Stock", "Price", "Today\nChange %", "Overall\nChange %", "Total PNL", "PNL", "Days\nIn", "Invested\nAmount", "Current\nValue", "Entry\nPrice", "Qty", "Entry\nDate"],
            column_config={
                "_row_idx": None,
                "Status": st.column_config.SelectboxColumn("Status", options=["Live", "Exited"], required=True),
                "Exit\nDate": st.column_config.TextColumn("Exit\nDate", help="YYYY-MM-DD"),
                "Price": st.column_config.NumberColumn(format="%.2f"),
                "Today\nChange %": st.column_config.NumberColumn(format="%.2f%%"),
                "Overall\nChange %": st.column_config.NumberColumn(format="%.2f%%"),
                "Total PNL": st.column_config.NumberColumn("Total PNL", format="%.2f", help="Standardized PNL based on ₹1 Lakh capital"),
                "PNL": st.column_config.NumberColumn("Actual PNL", format="%.2f", help="Actual PNL based on actual holding quantity"),
                "Invested\nAmount": st.column_config.NumberColumn(format="%.2f"),
                "Current\nValue": st.column_config.NumberColumn(format="%.2f"),
                "Entry\nPrice": st.column_config.NumberColumn(format="%.2f"),
                "Qty": st.column_config.NumberColumn(format="%.2f"),
                "Exit\nPrice": st.column_config.NumberColumn(format="%.2f"),
            }
        )

        if st.button("💾 Save Exit/Status Edits"):
            with st.spinner("Syncing updates to Google Sheets..."):
                for index, row in edited_df.iterrows():
                    gs_row = row["_row_idx"]
                    orig_record = next((item for item in st.session_state.portfolio_data if item["row_idx"] == gs_row), None)
                    
                    if orig_record:
                        if orig_record["status"] != row["Status"] or orig_record["exit_price"] != row["Exit\nPrice"] or orig_record["exit_date"] != row["Exit\nDate"]:
                            port_sheet.update_cell(gs_row, 5, str(row["Exit\nPrice"]) if pd.notnull(row["Exit\nPrice"]) else "")
                            port_sheet.update_cell(gs_row, 6, str(row["Exit\nDate"]) if pd.notnull(row["Exit\nDate"]) else "")
                            port_sheet.update_cell(gs_row, 7, row["Status"])
                
                del st.session_state.portfolio_data
                st.success("Updates saved!")
                st.rerun()
    else:
        st.info("Your portfolio is currently empty. Add a trade using the menu above to get started!")

# ---------------------------------------------------------
# CENTRAL MARKET SCANNER (MUTUALLY EXCLUSIVE TABS)
# ---------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def fetch_all_scanners_cached(watchlist_tuple, portfolio_tuple):
    buy_rows, strong_up_rows, up_rows, down_rows, sell_rows = [], [], [], [], []
    
    wl_dict = dict(watchlist_tuple)
    port_dict = dict(portfolio_tuple) # Ticker -> Entry Price for Live positions
    
    all_tickers = set(wl_dict.keys()).union(set(port_dict.keys()))
    
    for ticker in all_tickers:
        name = wl_dict.get(ticker, ticker)
        is_live_portfolio = ticker in port_dict
            
        try:
            stock = yf.Ticker(ticker)
            hist_d = stock.history(period="6mo", interval="1d").dropna(subset=["Close", "High", "Low"])
            hist_w = stock.history(period="2y", interval="1wk").dropna(subset=["Close", "High", "Low"])
            hist_m = stock.history(period="5y", interval="1mo").dropna(subset=["Close", "High", "Low"])
            
            def calculate_ta_indicators(hist):
                if hist is None or hist.empty or len(hist) <= 30: return None
                
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    hist.ta.ema(length=5, append=True)
                    hist.ta.ema(length=13, append=True)
                    hist.ta.macd(fast=8, slow=21, signal=9, append=True)
                    hist.ta.supertrend(length=10, multiplier=3, append=True)
                    hist.ta.rsi(length=14, append=True)

                st_col = get_safe_col(hist, "SUPERTd_")
                macd_col = get_safe_col(hist, "MACD_", ["MACDs_", "MACDh_"])
                macds_col = get_safe_col(hist, "MACDs_")
                
                if not st_col or not macd_col or not macds_col: return None
                
                def eval_row(row):
                    return {
                        "ema": "Bullish" if row.get("EMA_5", 0) > row.get("EMA_13", 0) else "Bearish",
                        "macd": "Bullish" if row[macd_col] > row[macds_col] else "Bearish",
                        "st": "Bullish" if row[st_col] == 1 else "Bearish",
                        "rsi": "Bullish" if row.get("RSI_14", 0) >= 55 else "Bearish",
                        "price": round(row["Close"], 2)
                    }

                latest_sig = eval_row(hist.iloc[-1])
                return latest_sig, hist, eval_row

            d_res = calculate_ta_indicators(hist_d)
            w_res = calculate_ta_indicators(hist_w)
            m_res = calculate_ta_indicators(hist_m)

            sig_d = d_res[0] if d_res else None
            sig_w = w_res[0] if w_res else None
            sig_m = m_res[0] if m_res else None
            
            if not sig_d and not sig_w and not sig_m:
                continue
            
            price = sig_d["price"] if sig_d else (sig_w["price"] if sig_w else (sig_m["price"] if sig_m else 0))
            
            row_base = {
                ("Daily", "EMA"): sig_d["ema"] if sig_d else "-",
                ("Daily", "MACD"): sig_d["macd"] if sig_d else "-",
                ("Daily", "ST"): sig_d["st"] if sig_d else "-",
                ("Daily", "RSI"): sig_d["rsi"] if sig_d else "-",
                (" ", " "): "", 
                ("Weekly", "EMA"): sig_w["ema"] if sig_w else "-",
                ("Weekly", "MACD"): sig_w["macd"] if sig_w else "-",
                ("Weekly", "ST"): sig_w["st"] if sig_w else "-",
                ("Weekly", "RSI"): sig_w["rsi"] if sig_w else "-",
                ("  ", "  "): "", 
                ("Monthly", "EMA"): sig_m["ema"] if sig_m else "-",
                ("Monthly", "MACD"): sig_m["macd"] if sig_m else "-",
                ("Monthly", "ST"): sig_m["st"] if sig_m else "-",
                ("Monthly", "RSI"): sig_m["rsi"] if sig_m else "-",
            }
            
            if is_live_portfolio:
                # 1. Sell Stock Logic (Only for active portfolio holdings)
                # Count Bearish indicators on Monthly timeframe
                m_bearish = sum(1 for k in ["ema", "macd", "st", "rsi"] if sig_m and sig_m[k] == "Bearish")
                
                if m_bearish >= 2:
                    entry_price = port_dict[ticker]
                    overall_pct = ((price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                    
                    sell_row = {
                        ("Stock", ""): name,
                        ("Price ($/₹)", ""): price,
                        ("Bought Price", ""): entry_price,
                        ("Overall %", ""): overall_pct,
                        **row_base
                    }
                    sell_rows.append(sell_row)
                    
            else:
                # 2. General Screener Logic (For Watchlist items NOT currently in portfolio)
                d_bulls = sum(1 for k in ["ema", "macd", "st", "rsi"] if sig_d and sig_d[k] == "Bullish")
                w_bulls = sum(1 for k in ["ema", "macd", "st", "rsi"] if sig_w and sig_w[k] == "Bullish")
                m_bulls = sum(1 for k in ["ema", "macd", "st", "rsi"] if sig_m and sig_m[k] == "Bullish")

                has_fresh_reversal = False
                if m_res:
                    _, m_hist, m_eval_fn = m_res
                    if len(m_hist) >= 34:
                        sig_m_prev1 = m_eval_fn(m_hist.iloc[-2])
                        sig_m_prev2 = m_eval_fn(m_hist.iloc[-3])
                        sig_m_prev3 = m_eval_fn(m_hist.iloc[-4])

                        for k in ["ema", "macd", "st"]:
                            if sig_m[k] == "Bullish":
                                if (sig_m_prev1[k] == "Bearish" and 
                                    sig_m_prev2[k] == "Bearish" and 
                                    sig_m_prev3[k] == "Bearish"):
                                    has_fresh_reversal = True
                                    break

                is_buy = (m_bulls >= 2) and has_fresh_reversal
                is_strong_uptrend = (m_bulls >= 2) and not is_buy
                is_uptrend = ((d_bulls >= 2) or (w_bulls >= 2)) and not (is_buy or is_strong_uptrend)

                row = {
                    ("Stock", ""): name,
                    ("Price ($/₹)", ""): price,
                    **row_base
                }

                if is_buy:
                    buy_rows.append(row)
                elif is_strong_uptrend:
                    strong_up_rows.append(row)
                elif is_uptrend:
                    up_rows.append(row)
                else:
                    down_rows.append(row) 
                
        except Exception:
            continue
            
    return buy_rows, strong_up_rows, up_rows, down_rows, sell_rows

with st.spinner("Scanning Market Data & Evaluating Timeframes..."):
    watchlist_tuple = tuple((item["ticker"], item.get("name", item["ticker"])) for item in st.session_state.watchlist_data if "ticker" in item)
    portfolio_tuple = tuple((p["ticker"], p["entry_price"]) for p in st.session_state.portfolio_data if p["status"] == "Live")
    
    buy_data, strong_up_data, up_data, down_data, sell_data = fetch_all_scanners_cached(watchlist_tuple, portfolio_tuple)

multi_cols_standard = pd.MultiIndex.from_tuples([
    ("Stock", ""), ("Price ($/₹)", ""),
    ("Daily", "EMA"), ("Daily", "MACD"), ("Daily", "ST"), ("Daily", "RSI"),
    (" ", " "), 
    ("Weekly", "EMA"), ("Weekly", "MACD"), ("Weekly", "ST"), ("Weekly", "RSI"),
    ("  ", "  "), 
    ("Monthly", "EMA"), ("Monthly", "MACD"), ("Monthly", "ST"), ("Monthly", "RSI")
])

multi_cols_sell = pd.MultiIndex.from_tuples([
    ("Stock", ""), ("Price ($/₹)", ""), ("Bought Price", ""), ("Overall %", ""),
    ("Daily", "EMA"), ("Daily", "MACD"), ("Daily", "ST"), ("Daily", "RSI"),
    (" ", " "), 
    ("Weekly", "EMA"), ("Weekly", "MACD"), ("Weekly", "ST"), ("Weekly", "RSI"),
    ("  ", "  "), 
    ("Monthly", "EMA"), ("Monthly", "MACD"), ("Monthly", "ST"), ("Monthly", "RSI")
])

df_buy = pd.DataFrame(buy_data, columns=multi_cols_standard) if buy_data else pd.DataFrame()
df_strong_uptrend = pd.DataFrame(strong_up_data, columns=multi_cols_standard) if strong_up_data else pd.DataFrame()
df_uptrend = pd.DataFrame(up_data, columns=multi_cols_standard) if up_data else pd.DataFrame()
df_downtrend = pd.DataFrame(down_data, columns=multi_cols_standard) if down_data else pd.DataFrame()
df_sell = pd.DataFrame(sell_data, columns=multi_cols_sell) if sell_data else pd.DataFrame()

# ---------------------------------------------------------
# 4. SELL STOCK TAB (PORTFOLIO WARNINGS)
# ---------------------------------------------------------
with tab_sell:
    st.write("")
    if not df_sell.empty:
        st.markdown("---")
        st.dataframe(apply_sell_styles(df_sell), hide_index=True, use_container_width=True)
    else:
        st.markdown("---")
        st.info("Great news! None of your active portfolio holdings are currently showing 2 or more Monthly Bearish indicators.")

# ---------------------------------------------------------
# 5. BUY SIGNAL TAB (FRESH REVERSALS)
# ---------------------------------------------------------
with tab_buy:
    st.write("")
    if not df_buy.empty:
        st.markdown("---")
        st.dataframe(apply_styles(df_buy), hide_index=True, use_container_width=True)
    else:
        st.markdown("---")
        st.info("No stocks in your watchlist currently meet the fresh 3-Month Reversal Buy Signal criteria.")

# ---------------------------------------------------------
# 6. STRONG UPTREND TAB (ESTABLISHED MONTHLY BULLS)
# ---------------------------------------------------------
with tab_strong_up:
    st.write("")
    if not df_strong_uptrend.empty:
        st.markdown("---")
        st.dataframe(apply_styles(df_strong_uptrend), hide_index=True, use_container_width=True)
    else:
        st.markdown("---")
        st.info("No stocks in your watchlist currently meet the Strong Uptrend conditions.")

# ---------------------------------------------------------
# 7. UPTREND STOCKS TAB
# ---------------------------------------------------------
with tab_uptrend:
    st.write("")
    if not df_uptrend.empty:
        st.markdown("---")
        st.dataframe(apply_styles(df_uptrend), hide_index=True, use_container_width=True)
    else:
        st.markdown("---")
        st.info("No stocks in your watchlist currently meet the Uptrend conditions.")

# ---------------------------------------------------------
# 8. DOWNTREND STOCKS TAB
# ---------------------------------------------------------
with tab_downtrend:
    st.write("")
    if not df_downtrend.empty:
        st.markdown("---")
        st.dataframe(apply_styles(df_downtrend), hide_index=True, use_container_width=True)
    else:
        st.markdown("---")
        st.info("No stocks in your watchlist currently meet the Downtrend conditions.")
