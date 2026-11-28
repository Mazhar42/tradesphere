# "One day at Meghna Traders": showcase script

Meghna Traders, its people, shops and companies are invented. Say so at the start. It is a story to show what the product does, not a real customer's results.

## Before the meeting (10 minutes)

1. Start Docker Desktop.
2. On the day itself, run `pwsh -ExecutionPolicy Bypass -File docs\demo\demo.ps1 -Reset`. It takes a few minutes and ends by printing the web address.
3. Open <http://localhost:5173>. Password for every login: `Meghna-Demo-2026`.
4. Open one normal window as the owner and one private window as the storekeeper, so you never sign out in front of the client.
5. Rehearse once, then run the reset again.

| Login | Person | Role |
|---|---|---|
| `demo.owner` | Rahim Uddin | Owner |
| `demo.manager` | Shirin Sultana | Manager |
| `demo.cashier` | Jamal Hossain | Cashier |
| `demo.storekeeper` | Karim Sheikh | Storekeeper |

Figures move a little with the date you reset. Read them from the screen instead of memorising them.

## Opening (1 minute, no screen)

> "Meet Rahim Uddin. He runs a dealership in Narayanganj: three companies, eighteen products, a dozen shops on credit and a busy counter.
>
> A few months ago his business lived in khata books, a calculator and his own memory. He knew he was selling. He did not know who owed him how much, which stock was about to expire, or what the companies still had to pay him back. Today is an ordinary day, three months after he started using TradeSphere."

## 1. Morning: the whole business on one page (2 minutes)

**As the owner, on Dashboard.**

> "Before his tea, Rahim sees today's sales and profit, what shops owe him, what is in the godown and what is in the bank. Two products are running low and something is close to expiry."

- Point at the sales chart. The gaps are Fridays.
- Open **Reports → Near expiry**: the 1 litre mango juice.

> "This juice arrived already close to its date. He can push it this week instead of finding it next month."

## 2. The shop that stopped paying (4 minutes). The key moment.

> "At ten o'clock the delivery man calls: Bhai Bhai Store wants two cartons of chanachur on credit. Selim bhai is a friendly man. In the old days, the answer was yes."

1. Type `bhai` in the search box at the top and open **Bhai Bhai Store**.
2. Show what the shop owes, its credit limit, and the statement: it paid regularly, then stopped.
3. Click **New invoice**. Choose product `SF-CHN-150`, quantity `2` cartons (৳3,648), leave **Paid now** empty, click **Save and post**.
4. The sale is **refused**: the shop can take less than ৳2,000 more on credit.

> "Nobody has to argue with a customer from memory. The limit Rahim set says it for him."

5. Go to **Customers**, and on the Bhai Bhai row click **Statement**, then **Receive payment**. Enter `50000` and click **Record receipt**.

> "Selim bhai sends fifty thousand."

6. Go to **Sales**, open the Bhai Bhai draft and click **Post invoice**. It goes through.
7. Show that stock left from a specific batch, oldest expiry first, and that **Print (A4)** and **Print (80 mm)** are there.

Then open **Customers → Aging report**.

> "And this is every shop, by how late it is. This is his collection list."

## 3. The truck arrives (2 minutes)

**Switch to the storekeeper window.**

> "Karim runs the godown. Notice what he cannot see: no sales, profit or money figures."

1. **Purchases**, open the **Confirmed** order from Padma Consumer Care (click the order number).
2. **Receive shipment**. Detergent: received `40`. Soap: received `25`. Give each a batch number (for example `PD-101`, `PD-102`) and an expiry date next year. **Post shipment**.
3. Open **Stock**: both products are full again.

> "Stock and what is owed to the company are updated from this one entry."

## 4. Money the companies owe him (3 minutes)

**Back to the owner. Open Company claims.**

> "Companies run schemes: a discount here, free bottles there. Rahim gives them to the shop today and the company pays him back later. If nobody writes it down, nobody claims it."

1. Show the open items, each tied to the sale that created it.
2. Choose **Sundarban Beverages**, tick the items, click **Create claim**. It is saved as a draft.
3. Open the **Claims** tab, open the draft and click **Submit claim**.
4. Go back to the **Claims** tab and show the earlier Shapla claim, partly paid, with the rest still tracked.

> "This is money that used to disappear quietly."

## 5. Is he actually making money? (2 minutes)

Open **Reports → Profit & loss**. Compare last month with this month using the dates.

> "Sales are in lakhs, but the margin is thin. After rent, salaries and transport, this is what is really left. Before, he found this out at the end of the year, if at all."

Show the **Cashbook** tab, then **Download Excel**.

## 6. If there is time (3 minutes)

- **Search for something new:** type `PCC-3010` at the top, then **Find a product that is not here**. It is known from Padma's price list but he does not sell it yet. Nothing is added without his say.
- **বাংলা:** click the language switch. The screens and figures change to Bangla.
- **Audit log:** who did what, and when.
- **Users & roles:** the four people and what each may do.

## Closing

> "Nothing Rahim did today was new work. He sold, collected, received and claimed, as he always has. The difference is that tonight he knows exactly where he stands.
>
> Which of these costs you the most today: late payments, expiry, or unclaimed schemes?"

Let them answer. Their answer tells you what to show next.

## Be careful about

- **This is an MVP running on your laptop.** Do not promise hosting, a phone app, offline use or a price.
- **Not ready to show:** Import data, Integrations, billing and opening balances. Do not open them unless asked.
- **Cashier login:** the cashier sees the total of cash and bank on the dashboard. Do not say the cashier sees no money.
- **Claims list:** it names sales as "Invoice #528" rather than by their invoice number.
- **Bangla wording** has not been checked by a native reviewer. Read the screens yourself first.
- **Company names** (Shapla, Padma, Sundarban) are made up. If the client deals with a company with a similar name, say it is not that one.

## If something goes wrong

| Problem | Fix |
|---|---|
| The sale was not refused | The data was already used. Run the reset. |
| "Today's sales" is zero | The data was built on an earlier day. Run the reset. |
| Page does not load | Check Docker Desktop is running, then run the script again. |
| Port already in use | Another project is using port 5173, 8000, 5432 or 5433. Close it. |
