import datetime
import re
import os
import time
import hashlib
from io import BytesIO
from num2words import num2words
import streamlit as st
from docxtpl import DocxTemplate
import docx  

st.set_page_config(page_title="Rehoboth Systems - Quotation Generator", layout="wide")

page_bg_css = """
<style>
@keyframes energyPulse {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}
.stApp {
    background: linear-gradient(-45deg, #050b14, #0a192f, #112240, #020c1b);
    background-size: 400% 400%;
    animation: energyPulse 15s ease infinite;
    color: #ffffff;
}
h1, h2, h3, p, label, .stMarkdown {
    color: #e6f1ff !important;
}
</style>
"""
st.markdown(page_bg_css, unsafe_allow_html=True)

st.title("⚡ Rehoboth Systems Quotation Generator")
st.markdown("Generates automated electrical & power solution quotations.")

# --- Sidebar Inputs ---
st.sidebar.header("Quote Configurations")
client_name = st.sidebar.text_input("Client Name", "PAL PENSION")
client_address = st.sidebar.text_area("Client Address", "289, Ajose Adeogun Street,\nVictoria Island, Lagos")
attention = st.sidebar.text_input("Kind Attention", "Babajide Sunmonu")
quote_ref = st.sidebar.text_input("Quote Reference", f"RSL/MJO/{datetime.date.today().strftime('%Y/%m')}/004")

st.sidebar.markdown("---")
st.sidebar.header("Financials & Multi-Options")
enable_option_2 = st.sidebar.checkbox("Enable Option 2 (Alternative Quote)", value=False)
markup_percent = st.sidebar.number_input("Markup Percentage (%)", value=0.0, step=5.0)

# --- DISCOUNT CONTROL ---
discount_percent = st.sidebar.number_input("Discount Percentage (%)", value=0.0, step=0.5)

apply_vat = st.sidebar.checkbox("Include VAT", value=False)
vat_percent = st.sidebar.number_input("VAT Percentage (%)", value=7.5, step=0.5) 

def amount_to_naira_words(amount):
    naira = int(amount)
    kobo = int(round((amount - naira) * 100))
    naira_text = num2words(naira, lang="en").title() + " Naira"
    if kobo > 0:
        kobo_text = num2words(kobo, lang="en").title() + " Kobo"
        return f"{naira_text}, {kobo_text}"
    return f"{naira_text} Only"

def parse_whatsapp_text(text):
    parsed_data = {}
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in lines:
        if line.startswith("[") and "]" in line:
            line = line.split("]", 1)[1].strip()
        if "markup" in line.lower() or "prepare quotation" in line.lower():
            continue

        price_match = re.search(r"[=#\₦\-]\s*([\d,]{3,})", line)
        price = None
        if price_match:
            price_str = price_match.group(1).replace(",", "")
            price = float(price_str)
            line = line.replace(price_match.group(0), "").strip()

        qty = 1
        qty_match = re.search(r"(\d+)\s*(pcs|pc|nos|pcs\.)?$", line, re.IGNORECASE)
        if qty_match:
            qty = int(qty_match.group(1))
            line = re.sub(r"(\d+)\s*(pcs|pc|nos|pcs\.)?$", "", line, re.IGNORECASE).strip()

        clean_desc = re.sub(r"[\-.\=]+$", "", line).strip()
        model_no = ""
        
        model_match = re.match(r"^([A-Z0-9\-_]+)\s+(.*)", clean_desc)
        if model_match:
            potential_model = model_match.group(1)
            if any(c.isdigit() for c in potential_model) and any(c.isalpha() for c in potential_model):
                model_no = potential_model
                clean_desc = model_match.group(2).strip()

        if clean_desc or model_no:
            key = (model_no + clean_desc).lower()
            if key not in parsed_data:
                parsed_data[key] = {"model": model_no, "desc": clean_desc, "qty": qty, "price": price}
    return list(parsed_data.values())

def process_items(items_list, prefix_key):
    edited_items = []
    sub_total = 0.0
    text_hash = hashlib.md5(str(items_list).encode('utf-8')).hexdigest()[:6]
    
    hcols = st.columns([1.5, 3, 1, 1.5])
    hcols[0].markdown("**Model No.**")
    hcols[1].markdown("**Description**")
    hcols[2].markdown("**Qty**")
    hcols[3].markdown("**Base Price**")
    
    for idx, item in enumerate(items_list):
        cols = st.columns([1.5, 3, 1, 1.5])
        mod = cols[0].text_input(f"{prefix_key}_mod_{idx}", value=item.get("model", ""), label_visibility="collapsed", key=f"{prefix_key}_mod_{idx}_{text_hash}")
        desc = cols[1].text_input(f"{prefix_key}_desc_{idx}", value=item["desc"], label_visibility="collapsed", key=f"{prefix_key}_desc_{idx}_{text_hash}")
        qty = cols[2].number_input(f"{prefix_key}_qty_{idx}", value=item["qty"], min_value=1, label_visibility="collapsed", key=f"{prefix_key}_qty_{idx}_{text_hash}")
        price = cols[3].number_input(f"{prefix_key}_price_{idx}", value=float(item["price"] or 0.0), label_visibility="collapsed", key=f"{prefix_key}_price_{idx}_{text_hash}")
        
        unit_price = price * (1 + (markup_percent / 100))
        line_total = unit_price * qty
        sub_total += line_total
        
        edited_items.append({
            "sn": idx + 1, "model": mod, "desc": desc, 
            "qty": qty, "unit_price": f"{unit_price:,.2f}", "line_total": f"{line_total:,.2f}"
        })
        
    discount_amount = sub_total * (discount_percent / 100)
    discounted_sub = sub_total - discount_amount
    vat_amount = (discounted_sub * (vat_percent / 100)) if apply_vat else 0.0
    grand_total = discounted_sub + vat_amount
    
    st.markdown(f"**Sub Total: ₦{sub_total:,.2f}**")
    if discount_amount > 0:
        st.markdown(f"**Discount ({discount_percent}%): -₦{discount_amount:,.2f}**")
        st.markdown(f"**Discounted Total: ₦{discounted_sub:,.2f}**")
    if apply_vat:
        st.markdown(f"**VAT ({vat_percent}%): ₦{vat_amount:,.2f}**")
    st.markdown(f"### **Grand Total: ₦{grand_total:,.2f}**")
    
    return edited_items, sub_total, discount_amount, vat_amount, grand_total

# --- UI BUILDER ---
prop_title_1 = st.text_input("Option 1 Title", "REPLACEMENT OF 10KVA UPS (10MINS RUNTIME)")
raw_text_1 = st.text_area("Paste Option 1 Items:", height=150)
items_list_1 = parse_whatsapp_text(raw_text_1) if raw_text_1 else []

if items_list_1:
    st.markdown("### Review Option 1")
    edited_1, sub_1, disc_1, vat_1, grand_1 = process_items(items_list_1, "opt1")

prop_title_2 = ""
edited_2, sub_2, disc_2, vat_2, grand_2 = [], 0, 0, 0, 0

if enable_option_2:
    st.markdown("---")
    prop_title_2 = st.text_input("Option 2 Title", "APC EASY UPS 10KVA WITH 100MINS RUNTIME")
    raw_text_2 = st.text_area("Paste Option 2 Items:", height=150)
    items_list_2 = parse_whatsapp_text(raw_text_2) if raw_text_2 else []
    
    if items_list_2:
        st.markdown("### Review Option 2")
        edited_2, sub_2, disc_2, vat_2, grand_2 = process_items(items_list_2, "opt2")

# --- NEW HELPER: Makes Word table cells bold ---
def make_cell_bold(cell, text):
    cell.text = "" # Clears any default formatting
    run = cell.paragraphs[0].add_run(text)
    run.bold = True

def populate_table(table, items, sub_tot, disc_amt, vat_amt, vat_pct, grand_tot):
    for item in items:
        row_cells = table.add_row().cells
        row_cells[0].text = str(item['sn'])
        row_cells[1].text = str(item['model']).strip() if item.get('model') else "-"
        row_cells[2].text = str(item['desc'])
        row_cells[3].text = str(item['unit_price'])
        row_cells[4].text = str(item['qty'])
        row_cells[5].text = str(item['line_total'])
        row_cells[6].text = "-"

    # Base Sub Total Row (Bolded)
    row_cells = table.add_row().cells
    make_cell_bold(row_cells[2], "SUB TOTAL")
    make_cell_bold(row_cells[5], f"{sub_tot:,.2f}")

    # Conditional Discount Rows (Bolded)
    if disc_amt > 0:
        row_cells = table.add_row().cells
        make_cell_bold(row_cells[2], f"DISCOUNT {discount_percent}%")
        make_cell_bold(row_cells[5], f"-{disc_amt:,.2f}")
        
        row_cells = table.add_row().cells
        make_cell_bold(row_cells[2], "DISCOUNTED TOTAL")
        make_cell_bold(row_cells[5], f"{(sub_tot - disc_amt):,.2f}")

    # VAT Row (Bolded)
    if apply_vat and vat_amt > 0:
        row_cells = table.add_row().cells
        make_cell_bold(row_cells[2], f"VAT {vat_pct}%")
        make_cell_bold(row_cells[5], f"{vat_amt:,.2f}")

    # Grand Total Row (Bolded)
    row_cells = table.add_row().cells
    make_cell_bold(row_cells[2], "TOTAL")
    make_cell_bold(row_cells[5], f"{grand_tot:,.2f}")

# --- GENERATOR ---
if items_list_1 and st.button("⚡ Energize & Generate Quotation"):
    with st.spinner("Processing..."):
        time.sleep(1)
        doc = DocxTemplate("template.docx")
        
        today_date = datetime.date.today()
        day = today_date.day
        suffix = 'th' if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        
        context = {
            "ref_number": quote_ref,
            "date": f"{day}{suffix} {today_date.strftime('%B, %Y')}",
            "client_name": client_name,
            "client_address": client_address,
            "attention": attention,
            "proposal_title_1": prop_title_1,
            "amount_in_words_1": amount_to_naira_words(grand_1),
            "enable_option_2": enable_option_2,
            "proposal_title_2": prop_title_2,
            "amount_in_words_2": amount_to_naira_words(grand_2) if enable_option_2 else ""
        }
        
        doc.render(context)
        doc.save("temp_rendered.docx")

        doc_docx = docx.Document("temp_rendered.docx")
        
        valid_tables = []
        for t in doc_docx.tables:
            if len(t.rows) > 0 and len(t.rows[0].cells) >= 7:
                header_text = str(t.rows[0].cells[2].text).strip().upper()
                if "DESCRIPTION" in header_text:
                    valid_tables.append(t)
        
        if len(valid_tables) > 0:
            populate_table(valid_tables[0], edited_1, sub_1, disc_1, vat_1, vat_percent, grand_1)
        
        if enable_option_2 and edited_2:
            if len(valid_tables) > 1:
                populate_table(valid_tables[1], edited_2, sub_2, disc_2, vat_2, vat_percent, grand_2)
            else:
                st.warning("⚠️ Could not find the second table. Please ensure Option 2 table is not deleted from template.docx!")

        bio = BytesIO()
        doc_docx.save(bio)
        os.remove("temp_rendered.docx")
        
        st.download_button(
            label="💾 Download Finished Document",
            data=bio.getvalue(),
            file_name=f"Quotation_{client_name.replace(' ', '_')}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        st.toast("⚡ Quotation Powered Up!", icon="🔌")