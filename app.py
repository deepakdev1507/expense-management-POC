import streamlit as st
import re
import fitz
from datetime import date, datetime
from openai import OpenAI
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

# ---------------------------
# Login Functionality
# ---------------------------
def login():
    st.title("Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        # Check credentials against st.secrets
        if (username == st.secrets["credentials"]["username"] and 
            password == st.secrets["credentials"]["password"]):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Invalid username or password")

# Check if user is authenticated; if not, show login form and stop further execution.
if "authenticated" not in st.session_state or not st.session_state["authenticated"]:
    login()
    st.stop()

# ---------------------------
# Initialize Clients using st.secrets
# ---------------------------
client = OpenAI(api_key=st.secrets["openai"]["api_key"])

ENDPOINT = st.secrets["azure"]["endpoint"]
KEY = st.secrets["azure"]["key"]

doc_int_client = DocumentIntelligenceClient(
    endpoint=ENDPOINT,
    credential=AzureKeyCredential(KEY)
)

def parse_receipt(file_obj):
    """
    Calls the 'prebuilt-receipt' model (DocumentIntelligenceClient 1.0.x).
    Extracts merchant_name, estimated_amount, zip_code, check_in, check_out,
    and receipt_file_content.
    """
    content_type = file_obj.type if file_obj.type else "application/octet-stream"
    file_bytes = file_obj.getvalue()

    # Analyze the receipt using the prebuilt-receipt model
    poller = doc_int_client.begin_analyze_document(
        "prebuilt-receipt",
        file_bytes,
        content_type=content_type
    )
    result = poller.result()

    parsed_data = {
        "merchant_name": "",
        "estimated_amount": 0.0,
        "zip_code": "",
        "check_in": None,
        "check_out": None,
        "logding_location": "",
        "receipt_file_content": ""  # New field to store receipt file content
    }

    # Save the complete receipt content from the result
    parsed_data["receipt_file_content"] = result.content

    if not result.documents:
        return parsed_data

    doc = result.documents[0]
    fields = doc.fields

    # Get merchant name from MerchantName
    merchant_field = fields.get("MerchantName")
    if merchant_field and merchant_field.value_string:
        parsed_data["merchant_name"] = merchant_field.value_string 

    # Get total amount from Total
    total_field = fields.get("Total")
    if total_field and total_field.content:
        parsed_data["estimated_amount"] = total_field.content 

    # Get lodging location from CountryRegion field
    country_region = fields.get("CountryRegion")
    if country_region and (country_region.value_country_region or country_region.value_address):
        if country_region.value_country_region and country_region.value_address:
            parsed_data["logding_location"] = country_region.value_country_region + country_region.value_address
        elif country_region.value_country_region:
            parsed_data["logding_location"] = country_region.value_country_region
        elif country_region.value_address:
            parsed_data["logding_location"] = country_region.value_address

    # Get zip code from MerchantAddress field
    address_field = fields.get("MerchantAddress")
    if address_field and address_field.value_address.postal_code:
        parsed_data["zip_code"] = address_field.value_address.postal_code

    # Extract dates: ArrivalDate, DepartureDate, TransactionDate
    arrival_field = fields.get("ArrivalDate")
    departure_field = fields.get("DepartureDate")
    transaction_field = fields.get("TransactionDate")

    def to_date(field_value):
        """Safely convert recognized field_value into a Python date."""
        if not field_value:
            return None
        try:
            if isinstance(field_value, datetime):
                return field_value.date()
            elif isinstance(field_value, date):
                return field_value
            else:
                return datetime.fromisoformat(str(field_value)).date()
        except:
            return field_value

    arrival_date = to_date(arrival_field.content if arrival_field else None) if arrival_field else None
    departure_date = to_date(departure_field.content if departure_field else None) if departure_field else None
    transaction_date = to_date(transaction_field.content if transaction_field else None) if transaction_field else None

    if arrival_date and departure_date:
        parsed_data["check_in"] = arrival_date
        parsed_data["check_out"] = departure_date
    else:
        if transaction_date:
            parsed_data["check_in"] = transaction_date
            parsed_data["check_out"] = transaction_date

    return parsed_data

def extract_text_from_pdf(pdf_path):
    """
    Extracts text from a given PDF file.
    :param pdf_path: Path to the PDF file.
    :return: Extracted text as a string.
    """
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text("text") + "\n"
        return text.strip()
    except Exception as e:
        return f"Error: {e}"

# ---------------------------
# STREAMLIT DEMO APP
# ---------------------------
def main():
    st.title("Expense Report - DocumentIntelligenceClient v1.0.x Demo")

    if "line_items" not in st.session_state:
        st.session_state.line_items = []

    if "draft_fields" not in st.session_state:
        st.session_state.draft_fields = {
            "expense_name": "",
            "expense_type": "Hotel/Lodging",
            "cost_center": "",
            "merchant_name": "",
            "lodging_location": "",
            "check_in": date.today(),
            "check_out": date.today(),
            "zip_code": "",
            "estimated_amount": 0.0,
            "receipt_file": ""  # New key to store receipt file content
        }

    st.header("Current Line Items")
    if st.session_state.line_items:
        for idx, item in enumerate(st.session_state.line_items):
            # Create a copy for display that omits the receipt file content
            display_item = item.copy()
            display_item.pop("receipt_file", None)
            st.markdown(f"**Line Item {idx+1}:**")
            st.json(display_item)
    else:
        st.write("No line items added yet.")

    with st.expander("Add a New Line Item"):
        with st.form("line_item_form", clear_on_submit=True):
            expense_name = st.text_input(
                "Expense Name or Ref No.",
                value=st.session_state.draft_fields["expense_name"]
            )
            expense_type = st.selectbox(
                "Expense Type",
                ["Hotel/Lodging", "Airfare", "Car Rental", "Meals", "Others", "Ride Share"],
                index=["Hotel/Lodging", "Airfare", "Car Rental", "Meals", "Others", "Ride Share"].index(
                    st.session_state.draft_fields["expense_type"]
                )
            )
            cost_center = st.text_input("Cost Center", value=st.session_state.draft_fields["cost_center"])
            merchant_name = st.text_input("Merchant Name", value=st.session_state.draft_fields["merchant_name"])
            lodging_location = st.text_input("Lodging Location", value=st.session_state.draft_fields["lodging_location"])
            check_in = st.text_input("Check In Date", value=st.session_state.draft_fields["check_in"])
            check_out = st.text_input("Check Out Date", value=st.session_state.draft_fields["check_out"])
            zip_code = st.text_input("Zip Code", value=st.session_state.draft_fields["zip_code"])
            estimated_amount = st.text_input(
                "Estimated Amount", 
                value=st.session_state.draft_fields["estimated_amount"]
            )

            # File uploader for receipt
            receipt_file = st.file_uploader("Upload Receipt", type=["png", "jpg", "jpeg", "pdf"])
            parse_button = st.form_submit_button("Parse Receipt")

            if parse_button and receipt_file is not None:
                with st.spinner("Analyzing receipt..."):
                    parsed_result = parse_receipt(receipt_file)

                def safe_date(val):
                    if isinstance(val, (date, datetime)):
                        return val if isinstance(val, date) else val.date()
                    elif isinstance(val, str):
                        try:
                            return datetime.fromisoformat(val).date()
                        except Exception:
                            return date.today()
                    return date.today()

                st.session_state.draft_fields["merchant_name"] = parsed_result["merchant_name"]
                st.session_state.draft_fields["estimated_amount"] = parsed_result["estimated_amount"]
                st.session_state.draft_fields["zip_code"] = parsed_result["zip_code"]
                st.session_state.draft_fields["lodging_location"] = parsed_result["logding_location"]
                if parsed_result["check_in"]:
                    st.session_state.draft_fields["check_in"] = parsed_result["check_in"]
                if parsed_result["check_out"]:
                    st.session_state.draft_fields["check_out"] = parsed_result["check_out"]
                # Update the receipt file content (stored but not shown in UI)
                st.session_state.draft_fields["receipt_file"] = parsed_result.get("receipt_file_content", "")

                st.success("Auto-filled from receipt. Please review before adding.")
                st.rerun()

            submitted = st.form_submit_button("Add This Line Item")
            if submitted:
                new_item = {
                    "expense_name": expense_name,
                    "expense_type": expense_type,
                    "cost_center": cost_center,
                    "merchant_name": merchant_name,
                    "lodging_location": lodging_location,
                    "check_in": str(check_in),
                    "check_out": str(check_out),
                    "zip_code": zip_code,
                    "estimated_amount": estimated_amount,
                    "receipt_file": st.session_state.draft_fields["receipt_file"]
                }
                st.session_state.line_items.append(new_item)

                # Reset draft fields
                st.session_state.draft_fields = {
                    "expense_name": "",
                    "expense_type": "Hotel/Lodging",
                    "cost_center": "",
                    "merchant_name": "",
                    "lodging_location": "",
                    "check_in": date.today(),
                    "check_out": date.today(),
                    "zip_code": "",
                    "estimated_amount": 0.0,
                    "receipt_file": ""
                }
                st.success("Line item added!")

    if st.button("Submit Expense Report"):
        st.write("**Final JSON Response (excluding receipt file content):**")
        # Create a copy for display that omits the receipt file content
        filtered_line_items = [{k: v for k, v in item.items() if k != "receipt_file"} 
                               for item in st.session_state.line_items]
        st.json(filtered_line_items)

        # Extract travel manual text for context
        pdf_text = extract_text_from_pdf("TRAVEL PROCEDURES MANUAL 2024.pdf")
        
        # When sending to OpenAI, include the complete line items with receipt file content
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Given to you is the travel manual or all the policies related to submitting expense: " + pdf_text},
                {"role": "system", "content": "You need to act as Policy violation checker, we will provide you with the line items of the expense, based on that you need to check and get back to with possible policy violations"},
                {"role": "system", "content": "Along with policy violation you should get back to user with suggestions. Here are the line items: " + str(st.session_state.line_items)},
                {"role": "system", "content": "Always respond back in json format with keys line_item_no, policy_violations (value should be None if no policy violation is there), suggestion (if any policy violations are there)"}
            ],
            response_format={ "type": "json_object" }
        )
        response = completion.choices[0].message
        st.json(response)

if __name__ == "__main__":
    main()
