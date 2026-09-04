# Full setup guide: run it locally and connect Dialogflow

This is the complete, in-order walkthrough to run the project on your own machine and wire it to a Dialogflow CX agent. Follow the parts top to bottom.

- Part 1: run the backend and website locally.
- Part 2: expose the backend with ngrok and get the public URL.
- Part 3: build the Dialogflow agent and plug the ngrok URL into the right places.
- Part 4: connect the chat widget and test.
- Part 5: what to do when the ngrok URL changes.
- Troubleshooting: the issues we actually hit.

---

## Part 1: Run the backend and website locally

### Prerequisites
- **Python 3.11 or 3.12** (not 3.14, some dependencies have no prebuilt wheels for it yet).
- **ngrok** installed (`brew install ngrok` on macOS).
- A Google Cloud account (for Part 3).

### Commands
From the project root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app:app --port 8099
```

Then open **http://localhost:8099**

One FastAPI process serves everything: the web page, the `/tools/*` endpoints (used by the Playbook), the `/webhook` endpoint (used by the Flow), and the live Server-Sent-Events stream. Port 8099 is used because 8080 is often taken; change it with `--port` if needed.

> Before the agent is wired up, the page shows a **Local demo** panel with buttons that trigger the banking actions, so you can confirm the live dashboard updates without Dialogflow.

Keep this terminal running.

---

## Part 2: Expose the backend with ngrok

Dialogflow runs in Google's cloud and cannot reach `localhost`, so ngrok gives your local backend a public HTTPS URL.

### One-time ngrok setup
Create a free account at https://dashboard.ngrok.com/signup, copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken, then:

```bash
ngrok config add-authtoken YOUR_AUTHTOKEN
```

### Start the tunnel (second terminal)
```bash
ngrok http 8099
```

ngrok prints a line like:
```
Forwarding  https://abcd-1234.ngrok-free.app -> http://localhost:8099
```

**Copy that `https://....ngrok-free.app` URL.** Throughout Part 3 it is referred to as `NGROK`. You will use it in exactly two places in Dialogflow:

- The **Webhook** URL:  `NGROK/webhook`   (note the `/webhook` on the end)
- The **Tool** server URL:  `NGROK`   (the base URL, no `/webhook`)

> Turn off any VPN or corporate proxy that inspects TLS (for example Zscaler) while running ngrok. TLS interception breaks ngrok's connection with a certificate error.

Keep this terminal running too.

---

## Part 3: Build the Dialogflow agent

Done in the Conversational Agents console: https://conversational-agents.cloud.google.com (the new name for Dialogflow CX).

### 3.1 Project and billing
1. Create or pick a Google Cloud project and **enable billing** on it. Dialogflow CX generative features (Playbooks, Gemini) require a billing account. Usage is a few cents for a demo, and an idle agent costs nothing.

### 3.2 Create the agent
1. **Create agent**, then **Build your own**.
2. Name `Banking Assistant`, location `global`, language English. Create.
3. Note the **Project ID** and **Agent ID** from the URL (`.../projects/PROJECT_ID/locations/global/agents/AGENT_ID/...`). You will need them in Part 4.

### 3.3 Enable the chat widget (Conversational Messenger)
1. **Integrations**, then **Conversational Messenger**, then **Connect**.
2. Environment **Draft**, access **Unauthenticated API**, UI style **Pop-out**.
3. "Restrict domain access" can be left empty (allow all), or add bare hosts `localhost` and `127.0.0.1` (no port, no path).
4. **Enable**, then **Done**.

If the Enable button is greyed out, billing is not enabled on the project. Fix that and retry.

### 3.4 Create the OpenAPI Tool (used by the Playbook)
1. **Tools**, then **Create**.
2. Name `Northwind Banking Actions`, type **OpenAPI**.
3. Paste the contents of `dialogflow/openapi-tools.yaml`, and set the `servers.url` line to your **`NGROK`** base URL (no `/webhook`, no trailing slash).
4. Authentication: leave the default. The backend ignores auth.
5. Save. If a **Test** button is offered, run `get_accounts`; you should get three accounts back (this confirms Dialogflow can reach your backend).

### 3.5 Create the `account` entity
1. **Manage** tab, then **Entity types**, then **Create**.
2. Display name `account`, kind **Map**:

| Value | Synonyms |
| --- | --- |
| `checking` | checking, everyday checking, current |
| `savings` | savings, rainy day, rainy day savings |
| `credit` | credit, credit card, platinum |

3. Save.

### 3.6 Register the Webhook (used by the Flow)
1. **Manage** tab, then **Webhooks**, then **Create**.
2. Display name `banking-backend`.
3. Webhook URL: **`NGROK/webhook`**

   Include the `/webhook` path. If you paste only the base URL, Dialogflow posts to `/`, the transfer fails, and the chat says "something went wrong".
4. Authentication none. Save.

### 3.7 Build the Transfer flow (deterministic)
The flow collects and confirms the transfer with fixed prompts, then calls the webhook.

1. **Build** tab. In **Flows**, rename **Default Start Flow** to `Transfer` (its more-options menu, then Rename), or create a new flow named `Transfer`.
2. In that flow, create a **page** named `Collect Transfer`.
3. On **Collect Transfer**, add three **parameters** (names must be exact):

| Parameter | Entity type | Required | Initial prompt (Agent dialogue) |
| --- | --- | --- | --- |
| `amount` | `@sys.number` | yes | How much would you like to transfer? |
| `from_account` | `@account` | yes | Which account should I transfer from? |
| `to_account` | `@account` | yes | Which account should I transfer to? |

   The prompt goes under Initial prompt fulfillment, then Agent responses, then **Agent dialogue**.
4. On the flow's **Start Page**, add a **Route**: Condition **Customize expression** `true`, Transition **Page** then **Collect Transfer**. (The Start Page cannot hold parameters, so it just forwards into the collect page.)
5. On the **Collect Transfer** page, add a **Route**:
   - Condition **Customize expression**: `$page.params.status = "FINAL"`
   - Fulfillment, then **Webhook settings**: enable **banking-backend**, tag `transfer_funds`
   - Transition **Playbook** then **Banking Assistant**
   - Save.

### 3.8 Create the Playbook (generative) and the handoff
1. **Playbooks**, then **Create**, type **Routine**. Name `Banking Assistant`.
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
   When you type `${`, pick **Flow** then **Transfer** so it links.
4. Attach the `Northwind Banking Actions` tool to the playbook. Save.

### 3.9 Make the Playbook the start resource
1. Agent settings (gear icon), then **General**, then **Conversation start**.
2. Select **Playbook** and choose **Banking Assistant**. Save.

---

## Part 4: Connect the widget and test

1. In `frontend/config.js`, set `PROJECT_ID` and `AGENT_ID` to your agent's values, `LOCATION` to `global`.
2. Reload http://localhost:8099. The real chat bubble appears bottom-right.
3. Try:
   - `what's my balance` (Playbook lists the accounts)
   - `transfer 100 from checking to savings`, follow the prompts, confirm (Flow, dashboard updates live)
   - `pay 40 to the water company` (Playbook)
   - `transfer 1000000 from checking to savings` (rejected with "insufficient funds")

---

## Part 5: When the ngrok URL changes

On the free tier, ngrok gives a **new URL every time you restart it**. After a restart, update the new URL in **two** places:

1. Manage, then Webhooks, then `banking-backend`: set URL to `NEW_NGROK/webhook`.
2. Tools, then Northwind Banking Actions: set `servers.url` to `NEW_NGROK` (base, no `/webhook`).

`frontend/config.js` holds project/agent IDs, not the ngrok URL, so it does not need changing.

---

## Troubleshooting

- **Transfer says "something went wrong"**: the Webhook URL is missing the `/webhook` path. It must be `NGROK/webhook`.
- **ngrok fails with a TLS certificate error**: a VPN or proxy (for example Zscaler) is inspecting TLS. Turn it off.
- **"Enable" greyed out on Conversational Messenger**: billing is not enabled on the project.
- **The chat bubble ignores the playbook**: the agent's Conversation start is still a Flow. Set it to the Playbook (3.9) and reload the page for a fresh session.
- **pip fails building wheels**: you are on Python 3.14. Use 3.11 or 3.12.
- **Backend not reachable from Dialogflow**: confirm both terminals are running (uvicorn and ngrok) and that the ngrok URL in Dialogflow matches the current one.
