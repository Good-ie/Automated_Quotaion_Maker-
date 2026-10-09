# ⚡ Rehoboth Systems Automated Quotation Generator

A **Custom CPQ (Configure, Price, Quote) Automation Engine** specifically engineered for the electrical power and engineering industry. 

This proprietary Streamlit application acts as a bridge between quick field communications (like WhatsApp messages) and highly professional, client-ready financial documents. It transforms a tedious, error-prone administrative chore into a secure, one-click software solution that empowers staff to generate flawless proposals in seconds.

### ⚙️ Core Capabilities
* **Intelligent Data Processing:** Uses advanced pattern recognition to parse unstructured WhatsApp text, extracting product names, quantities, and raw prices.
* **Smart Model Extraction:** Automatically detects alphanumeric part numbers (e.g., CAD50M7) and isolates them into a dedicated column.
* **Dynamic Financial Engine:** Instantly calculates custom markups, optional VAT, subtotals, and automatically translates large Naira totals into formal English text (e.g., "Five Million Naira Only").
* **Secure Document Generation:** Bypasses standard Word tags to forcefully construct custom table rows inside a proprietary template. Files are processed entirely in the computer’s invisible memory (`BytesIO`) and pushed straight to the user's Downloads folder to protect client data and prevent folder clutter.

---

### ⚠️ Important Prerequisite: The 7-Column Word Template
For security and privacy reasons, the proprietary `template.docx` file is **not included** in this repository. 

To run this application, you must place a Microsoft Word document named `template.docx` in the main folder. The document must contain a standard Word table with a single row of headers at the top containing **exactly 7 columns** in this order:
`S/N` | `MODEL NO` | `DESCRIPTIONS` | `UNIT COST =N=` | `QTY` | `TOTAL COST =N=` | `AVAILABILITIES`

---

### 🚀 How to Use the App (Worker Instructions)

1. Ensure Python is installed on your computer.
2. Open your terminal in this folder and install the required packages:
   ```bash
   pip install -r requirements.txt