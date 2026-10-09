import datetime
import re
import os
import time
import hashlib
from io import BytesIO  # NEW: Handles the file in memory
from num2words import num2words
import streamlit as st
from docxtpl import DocxTemplate
import docx  

# --- Page Config ---
st.set_page_config(page_title="Rehoboth Systems - Quotation Generator", layout="wide")

# --- Custom "Power & Energy" Animated Background ---
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
client_name = st.sidebar.text_input("Client Name", "Virgin Pet")
client_address = st.sidebar.text_area("Client Address", "KM38 Abeokuta Motor Road\nSango-Ota, Ogun State")
attention = st.sidebar.text_input("Kind Attention", "The Procurement Manager")
proposal_title = st.sidebar.text_input("Proposal Title", "Schneider TeSys D Contactors Relays")
markup_percent = st.sidebar.number_input("Markup Percentage (%)", value=30.0, step=5.0)

# --- OPTIONAL TOGGLES ---
show_model = st.sidebar.checkbox("Include Model Numbers", value=True)
apply_vat = st.sidebar.checkbox("Include VAT", value=False)
vat_percent = st.sidebar.number_input("VAT Percentage (%)", value=7.5, step=0.5) 

quote_ref = st.sidebar.text_input("Quote Reference", f"RSL/MJO/{datetime.date.today().strftime('%Y/%m')}/001")

def amount_to_naira_words(amount):
    naira = int(amount)
    kobo = int(round((amount - naira) * 100))
    naira_text = num2words(naira, lang="en").title() + " Naira"
    if kobo > 0:
        kobo_text = num2words(kobo, lang="en").title() + " Kobo"
        return f"{naira_text}, {kobo_text}"
    return f"{naira_text} Only"

# --- Main Area: Input Text Parsing ---
st.subheader("1. Paste WhatsApp Raw Text")
raw_text = st.text_area("Paste the item list and pricing message here:", height=250)
text_hash = hashlib.md5(raw_text.encode('utf-8')).hexdigest()[:6] 

def parse_whatsapp_text(text):
    items = []
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    parsed_data = {}

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
            else:
                if price is not None:
                    parsed_data[key]["price"] = price
                if qty != 1:
                    parsed_data[key]["qty"] = qty

    return list(parsed_data.values())

items_list = []
if raw_text:
    items_list = parse_whatsapp_text(raw_text)

# --- NEW EDITABLE GRID INTERFACE ---
st.subheader("2. Review & Adjust Extracted Items")
edited_items = []

if items_list:
    st.markdown("Make any final adjustments or type in model numbers below before generating:")
    
    if show_model:
        hcols = st.columns([1.5, 3, 1, 1.5])
        hcols[0].markdown("**Model No.**")
        hcols[1].markdown("**Description**")
        hcols[2].markdown("**Qty**")
        hcols[3].markdown("**Base Price**")
    else:
        hcols = st.columns([3, 1, 1.5])
        hcols[0].markdown("**Description**")
        hcols[1].markdown("**Qty**")
        hcols[2].markdown("**Base Price**")

    sub_total = 0.0
    
    for idx, item in enumerate(items_list):
        if show_model:
            cols = st.columns([1.5, 3, 1, 1.5])
            mod = cols[0].text_input(f"mod_{idx}", value=item.get("model", ""), label_visibility="collapsed", key=f"mod_{idx}_{text_hash}")
            desc = cols[1].text_input(f"desc_{idx}", value=item["desc"], label_visibility="collapsed", key=f"desc_{idx}_{text_hash}")
            qty = cols[2].number_input(f"qty_{idx}", value=item["qty"], min_value=1, label_visibility="collapsed", key=f"qty_{idx}_{text_hash}")
            price = cols[3].number_input(f"price_{idx}", value=float(item["price"] or 0.0), label_visibility="collapsed", key=f"price_{idx}_{text_hash}")
        else:
            cols = st.columns([3, 1, 1.5])
            mod = ""
            desc = cols[0].text_input(f"desc_{idx}", value=item["desc"], label_visibility="collapsed", key=f"desc_{idx}_{text_hash}")
            qty = cols[1].number_input(f"qty_{idx}", value=item["qty"], min_value=1, label_visibility="collapsed", key=f"qty_{idx}_{text_hash}")
            price = cols[2].number_input(f"price_{idx}", value=float(item["price"] or 0.0), label_visibility="collapsed", key=f"price_{idx}_{text_hash}")
        
        unit_price = price * (1 + (markup_percent / 100))
        line_total = unit_price * qty
        sub_total += line_total
        
        edited_items.append({
            "sn": idx + 1,
            "model": mod,
            "desc": desc,
            "qty": qty,
            "unit_price": f"{unit_price:,.2f}",
            "line_total": f"{line_total:,.2f}"
        })
    
    vat_amount = (sub_total * (vat_percent / 100)) if apply_vat else 0.0
    grand_total = sub_total + vat_amount
    
    st.markdown("---")
    st.markdown(f"### **Sub Total: ₦{sub_total:,.2f}**")
    if apply_vat:
        st.markdown(f"### **VAT ({vat_percent}%): ₦{vat_amount:,.2f}**")
    st.markdown(f"### **Grand Total: ₦{grand_total:,.2f}**")
    st.info(f"**Amount in Words:** {amount_to_naira_words(grand_total)}")

# --- Template Generator Function ---
def generate_docx_from_template(client, client_addr, attn, prop_title, ref, items, sub_tot, vat_amt, vat_pct, grand_tot):
    doc = DocxTemplate("template.docx")
    
    today_date = datetime.date.today()
    day = today_date.day
    suffix = 'th' if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    formatted_date = f"{day}{suffix} {today_date.strftime('%B, %Y')}"

    context = {
        "ref_number": ref,
        "date": formatted_date,
        "client_name": client,
        "client_address": client_addr,
        "attention": attn,
        "proposal_title": prop_title,
        "amount_in_words": amount_to_naira_words(grand_tot),
    }
    
    doc.render(context)
    temp_file = "temp_rendered.docx"
    doc.save(temp_file)

    doc_docx = docx.Document(temp_file)
    table = doc_docx.tables[0] 

    for item in items:
        row_cells = table.add_row().cells
        
        row_cells[0].text = str(item['sn'])
        row_cells[1].text = str(item['model']).strip() if item.get('model') and str(item['model']).strip() else "-"
        row_cells[2].text = str(item['desc'])
        row_cells[3].text = str(item['unit_price'])
        row_cells[4].text = str(item['qty'])
        row_cells[5].text = str(item['line_total'])
        row_cells[6].text = "-"

    row_cells = table.add_row().cells
    row_cells[2].text = "SUB TOTAL"
    row_cells[5].text = f"{sub_tot:,.2f}"

    if apply_vat and vat_amt > 0:
        row_cells = table.add_row().cells
        row_cells[2].text = f"VAT {vat_pct}%"
        row_cells[5].text = f"{vat_amt:,.2f}"

    row_cells = table.add_row().cells
    row_cells[2].text = "TOTAL"
    row_cells[5].text = f"{grand_tot:,.2f}"

    # NEW: Save directly into system memory instead of the project folder
    bio = BytesIO()
    doc_docx.save(bio)
    
    if os.path.exists(temp_file):
        os.remove(temp_file)
        
    return bio.getvalue()

# --- Custom Action & Download ---
if edited_items and st.button("⚡ Energize & Generate Quotation"):
    with st.spinner("⚡ Powering up calculations and injecting data..."):
        time.sleep(1) 
        try:
            file_data = generate_docx_from_template(
                client_name, client_address, attention, proposal_title, quote_ref, 
                edited_items, sub_total, vat_amount, vat_percent, grand_total
            )
            
            # Formats the download file name cleanly
            file_name = f"Quotation_{client_name.replace(' ', '_')}.docx"
            
            st.download_button(
                label="💾 Download Finished Document",
                data=file_data,
                file_name=file_name,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            
            st.toast("⚡ Quotation Powered Up Successfully!", icon="🔌")
            st.success("Sequence complete. Your document is ready to be saved to your Downloads folder.")
            
        except Exception as e:
            st.error(f"System Error: You must ensure your template.docx has exactly 7 columns! {e}")