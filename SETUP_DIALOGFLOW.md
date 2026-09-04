# Building the agent in Dialogflow CX (step by step)

This builds the **hybrid** agent: a generative **Playbook** for the conversation, plus a deterministic **Flow** for money transfers. Both call your backend, which runs the action through MCP.

Everything here is done in the **Conversational Agents** console: https://conversational-agents.cloud.google.com (this is the new name for Dialogflow CX).

## 0. Prerequisites

- Your backend and ngrok are running (see `README.md`). Keep the ngrok URL handy; below it is written as `NGROK` (for example `https://abcd-1234.ngrok-free.app`).
- A Google Cloud project with **billing enabled**. Dialogflow CX generative features (Playbooks, Gemini) require a billing account. Usage is a few cents for a demo, and an idle agent costs nothing.

---

## 1. Create the agent

1. Open the console and select your project.
2. **Create agent** and choose **Build your own**.
3. Name it `Banking Assistant`, location `global`, language English. Create.

Note the **Project ID** and **Agent ID** from the URL:
`.../projects/PROJECT_ID/locations/global/agents/AGENT_ID/...`

---

## 2. Enable the chat widget (Conversational Messenger)

1. **Integrations** in the left nav, then **Conversational Messenger**, then **Connect**.
2. Environment: **Draft**. Access: **Unauthenticated API**. UI style: **Pop-out**.
3. Under "Restrict domain access" you can leave it empty (allow all) or add bare hosts like `localhost` and `127.0.0.1` (no port, no path).
4. Click **Enable**, then **Done**.

If the Enable button is greyed out, it is almost always **billing not enabled** on the project. Enable billing and retry.

---

## 3. Create the OpenAPI Tool (used by the Playbook)

1. **Tools** in the left nav, then **Create**.
2. Name: `Northwind Banking Actions`. Type: **OpenAPI**.
3. Schema: paste the contents of `dialogflow/openapi-tools.yaml`, and set the `servers.url` line to your `NGROK` base URL (no `/webhook`, no trailing slash).
4. Authentication: leave the default (**Service agent token**). The backend ignores auth, so any option works.
5. Save. If there is a **Test** button, run `get_accounts`; you should get three accounts.

---

## 4. Create the `account` entity

So the flow can recognize account names.

1. **Manage** tab, then **Entity types**, then **Create**.
2. Display name: `account`, kind **Map**.
3. Entries:

| Value | Synonyms |
| --- | --- |
| `checking` | checking, everyday checking, current |
| `savings` | savings, rainy day, rainy day savings |
| `credit` | credit, credit card, platinum |

4. Save.

---

## 5. Register the Webhook (used by the Flow)

1. **Manage** tab, then **Webhooks**, then **Create**.
2. Display name: `banking-backend`.
3. Webhook URL: `NGROK/webhook`

   **Include the `/webhook` path.** This is a common mistake: if you paste only the base URL, Dialogflow posts to `/` and the transfer fails with "something went wrong".
4. Authentication: none. Save.

---

## 6. Build the Transfer flow (deterministic)

The flow collects the transfer details with fixed prompts, then calls the webhook.

**6a. The flow and page**
1. **Build** tab. In **Flows**, you can rename the **Default Start Flow** to `Transfer` (its more options menu, then Rename), or create a new flow named `Transfer`.
2. In that flow, create a **page** named `Collect Transfer`.

**6b. Parameters (the form) on the Collect Transfer page**
Add three parameters. The names must be exact, the backend reads them.

| Parameter | Entity type | Required | Initial prompt (Agent dialogue) |
| --- | --- | --- | --- |
| `amount` | `@sys.number` | yes | How much would you like to transfer? |
| `from_account` | `@account` | yes | Which account should I transfer from? |
| `to_account` | `@account` | yes | Which account should I transfer to? |

The prompt goes under **Initial prompt fulfillment**, then **Agent responses**, then **Agent dialogue**.

**6c. A route on the flow's Start Page**
The flow enters at its Start Page (which cannot hold parameters), so send it to the Collect Transfer page:
1. Open the flow's **Start Page**, then **Routes**, then **+**.
2. Condition: **Customize expression**, value `true`.
3. Transition: **Page**, then **Collect Transfer**. Save.

**6d. The execute route on the Collect Transfer page**
1. On the **Collect Transfer** page, **Routes**, then **+**.
2. Condition: **Customize expression**, value:
   ```
   $page.params.status = "FINAL"
   ```
   (fires once all three parameters are filled)
3. Fulfillment, then **Webhook settings**: enable **banking-backend**, tag `transfer_funds`.
4. Transition: **Playbook**, then **Banking Assistant** (returns control to the generative agent after the transfer).
5. Save.

---

## 7. Create the Playbook (generative) and the handoff

1. **Playbooks** in the left nav, then **Create**, type **Routine**. Name it `Banking Assistant`.
2. **Goal**:
   ```
   You are Northwind Bank's friendly virtual assistant. Help customers check
   balances, review transactions, transfer money between their own accounts, and
   pay bills, using the Northwind Banking Actions tool. Always base numbers on tool
   results, never invent them. Keep replies short, clear, and friendly.
   ```
3. **Instructions**:
   ```
   - Greet the customer warmly, but only on the first turn.
   - Balances: when asked about a balance, immediately call get_accounts. List each
     account on its own line as "Account name: $amount". Do not ask which account
     unless the customer clearly wants only one.
   - Transactions: call get_transactions (pass account_id if an account is named)
     and show the most recent few as "date - description - $amount".
   - Transfers: when the customer wants to move or transfer money, do NOT call
     transfer_funds. Instead hand off to ${FLOW: Transfer} to collect and confirm
     the details, then return here when it finishes.
   - Pay a bill: collect payee and amount, confirm in one short sentence, call
     pay_bill, then report the result.
   - If a tool returns ok=false, apologize briefly and state the exact reason. Never
     claim success if it failed.
   - Keep replies to 1-3 short sentences, plain language, format money as $X.XX.
   - Only handle banking topics.
   ```
   When you type `${` in the instructions, pick **Flow**, then **Transfer** from the autocomplete so it links.
4. **Attach the tool**: add `Northwind Banking Actions` to the playbook's available tools.
5. Save.

---

## 8. Make the Playbook the start resource

So the conversation starts with the generative agent (and hands off to the flow only for transfers).

1. Agent settings (gear icon), then **General**, then **Conversation start**.
2. Select **Playbook** and choose **Banking Assistant**. Save.

---

## 9. Wire the widget to your page

1. In `frontend/config.js`, set `PROJECT_ID` and `AGENT_ID` to your agent's values, `LOCATION` to `global`.
2. Reload the page. The real chat bubble appears in the bottom-right.

---

## 10. Test

On the page, open the chat and try:

- `what's my balance` (Playbook, lists accounts)
- `transfer 100 from checking to savings`, then follow the prompts and confirm (Flow, updates the dashboard live)
- `pay 40 to the water company` (Playbook)

---

## Troubleshooting (the things that actually bit us)

- **Transfer says "something went wrong"**: the Webhook URL is missing the `/webhook` path. It must be `NGROK/webhook`, not just `NGROK`.
- **ngrok URL changed** (it changes on every restart, free tier): update it in **two** places, the Webhook URL (`NGROK/webhook`) and the Tool's `servers.url` (`NGROK`, base). The `frontend/config.js` values are project/agent IDs, not the ngrok URL, so they do not change.
- **ngrok fails with a TLS certificate error**: a VPN or corporate proxy (for example Zscaler) is doing TLS inspection. Turn it off while running ngrok.
- **"Enable" greyed out on Conversational Messenger**: billing is not enabled on the project.
- **The chat bubble ignores your playbook**: the agent's Conversation start is still set to a Flow. Set it to the Playbook (step 8), and reload the page for a fresh session.
- **Python install fails building wheels**: you are on Python 3.14. Use 3.11 or 3.12.
