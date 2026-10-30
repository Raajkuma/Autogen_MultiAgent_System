"""
Streamlit Web Application providing Support Chat, Storefront Management,
Human-in-the-loop Approval Panels, and Analytics Dashboards.
"""
import asyncio
import pandas as pd
import streamlit as st
import store

# Safely import agent functions with fallbacks if config variables are internal
try:
    from support_agents import BASE_URL, MODEL_NAME, handle_query
except ImportError:
    from support_agents import handle_query
    BASE_URL = "http://localhost:12434/engines/v1"
    MODEL_NAME = "ai/qwen3-vl:2B-BF16"

st.set_page_config(page_title="Multi-Agent Customer Support Hub", page_icon="🎧", layout="wide")
st.title("🎧 Customer Support & Back-Office Suite")

with st.sidebar:
    st.subheader("Configuration")
    st.code(f"model:    {MODEL_NAME}\nendpoint: {BASE_URL}", language="text")
    st.subheader("Agents Network")
    st.markdown(
        "- 🧭 **Supervisor** – Router & Reviewer\n"
        "- 🛒 **Sales** – Products & Specs\n"
        "- 💳 **Payment** – Order Management & Policy Approvals"
    )
    if st.button("🗑️ Reset Database & Chat"):
        store.reset_db()
        st.session_state.clear()
        st.rerun()

tab_chat, tab_shop, tab_approvals, tab_data = st.tabs([
    "💬 Support Chat", "🛍️ Store Front", "⏳ Pending Approvals", "📊 Business & Policy Data"
])

# ----------------------------- Tab 1: Chat -------------------------------- #
with tab_chat:
    st.session_state.setdefault("messages", [])

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("trace"):
                with st.expander("Agent Execution Trace"):
                    st.markdown("\n\n".join(m["trace"]))

    async def run_query(prompt: str, history: list[dict]):
        trace: list[str] = []
        final = ""
        status = st.status("Agents working...", expanded=True)

        async for kind, d in handle_query(prompt, history):
            if kind == "route":
                msg = f"🧭 **Supervisor** routed query to **{d['route']}**"
                trace.append(msg)
                status.markdown(msg)
            elif kind == "tool_call":
                msg = f"🔧 **{d['source']}** executing `{d['name']}({d['args']})`"
                trace.append(msg)
                status.markdown(msg)
            elif kind == "tool_result":
                msg = f"📥 `{d['name']}` returned result."
                trace.append(msg)
                status.markdown(msg)
            elif kind == "message":
                msg = f"💬 **{d['source']}**: {d['content']}"
                trace.append(msg)
                status.markdown(msg)
            elif kind == "final":
                final = d["content"]

        status.update(label="Response Complete", state="complete", expanded=False)
        return final, trace

    if prompt := st.chat_input("How can we help you today?"):
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            try:
                history_for_agent = st.session_state.messages[:-1]
                final_ans, trace_logs = asyncio.run(run_query(prompt, history_for_agent))
            except Exception as e:
                final_ans, trace_logs = f"⚠️ System Error: `{e}`", []

            st.markdown(final_ans)
            if trace_logs:
                with st.expander("Agent Execution Trace"):
                    st.markdown("\n\n".join(trace_logs))

        st.session_state.messages.append({"role": "assistant", "content": final_ans, "trace": trace_logs})
        st.rerun()

# ----------------------------- Tab 2: Shop -------------------------------- #
with tab_shop:
    st.subheader("Interactive E-Commerce Store")
    c1, c2 = st.columns([1, 2])

    with c1:
        st.markdown("### Place New Order")
        cust = st.selectbox("Customer Account", store.CUSTOMERS)
        prods = {f"{p['name']} (${p['price']})": p['id'] for p in store.list_products()}
        sel_prod = st.selectbox("Select Product", list(prods.keys()))
        qty = st.number_input("Quantity", min_value=1, max_value=10, value=1)

        if st.button("🛒 Place Order", use_container_width=True):
            res = store.place_order(cust, prods[sel_prod], qty)
            if res["ok"]:
                st.success(f"Order created: `{res['order']['id']}`")
                st.rerun()
            else:
                st.error(res["error"])

    with c2:
        st.markdown("### Orders & Management")
        all_orders = store.list_orders()
        for o in reversed(all_orders):
            with st.expander(f"📦 Order {o['id']} - {o['customer']} | {o['product']} (${o['total']:.2f}) - Status: {o['status'].upper()}"):
                st.write(f"**Date:** {o['date']} | **Payment:** {o['payment_status']} | **Refund/Service:** {o['refund_status']}")
                st.json(o["events"])
                if o["status"] == "placed" and st.button("❌ Cancel & Auto-Refund", key=f"can_{o['id']}"):
                    store.cancel_order(o["id"])
                    st.success("Cancelled and refunded!")
                    st.rerun()

# -------------------------- Tab 3: Approvals ------------------------------ #
with tab_approvals:
    head_col1, head_col2 = st.columns([4, 1])
    with head_col1:
        st.subheader("⏳ Pending Approvals & Cancellation Reviews")
    with head_col2:
        if st.button("🔄 Refresh List", use_container_width=True):
            st.rerun()

    pending = store.list_approvals(status="pending")
    if not pending:
        st.info("No pending review requests.")
    else:
        for a in pending:
            with st.container(border=True):
                col_info, col_act = st.columns([3, 1])
                with col_info:
                    # Fixed: Use 'id' instead of 'order_id' since list_approvals returns order dictionaries
                    st.markdown(f"### Order ID: `{a['id']}`")
                    st.write(f"**Customer:** {a['customer']} | **Product:** {a['product']}")
                    st.write(f"**Status:** {a['status'].upper()} | **Total Amount:** `${a['total']:.2f}`")
                    st.json(a["events"])
                with col_act:
                    remarks = st.text_area("Manager Remarks", key=f"rem_{a['id']}")
                    if st.button("✅ Approve & Pay Refund", key=f"app_{a['id']}", use_container_width=True):
                        store.decide_order_approval(a['id'], True, note=remarks)
                        st.success("Approved! Refund processed.")
                        st.rerun()
                    if st.button("❌ Reject", key=f"rej_{a['id']}", use_container_width=True):
                        store.decide_order_approval(a['id'], False, note=remarks)
                        st.warning("Request rejected.")
                        st.rerun()
# ---------------------------- Tab 4: Data --------------------------------- #
with tab_data:
    st.subheader("📊 Business Data & Policies")
    db = store.load_db()
    for pol in db["policies"]:
        with st.expander(pol["title"]):
            st.text(pol["text"])