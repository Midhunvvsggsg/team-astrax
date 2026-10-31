# Subscription Management System

Build one system to run plans, subscriptions and recurring billing in one place.

## 1) The Challenge

Build one unified software platform that runs the full life of a subscription business,
from setting up products and plans to sending invoices, collecting payments and reporting
revenue. Think of it as the digital backbone of a company that earns its money every
month, not just once. Read the scenes that follow as things that actually happen in an
ordinary billing month, and design the system that makes each of them easy.

## 2) Meet AstraFlow Software

AstraFlow Software sells everything on subscription: business software billed by the
month or the year, add-ons customers switch on and off, and a few products that come in
different variants. Customers range from small shops on a monthly plan to large companies
on yearly contracts. An admin sets up products, plans, taxes and discounts, a small
internal team handles customers day to day, and customers want to see and manage their
own subscriptions without having to email anyone.

### How Things Run Today
- Customer subscriptions: tracked in a spreadsheet
- Renewals: remembered by whoever notices first
- Invoices: typed up by hand every month
- Payments: matched to invoices by scrolling through bank statements
- Revenue reports: built manually at the end of each quarter

Renewals get missed, invoices go out wrong, and nobody can say how much recurring revenue
the company actually has.

## 3) A Month at AstraFlow

The scenes below follow one billing month at AstraFlow Software, from a new customer
creating an account to the finance lead reviewing the numbers.

### A new customer signs up
A small business finds AstraFlow online and creates an account with their email and a
password. Each email can belong to only one account, and the password must be more than
eight characters, with an uppercase letter, a lowercase letter and a special character.
If they forget it later, a reset link should arrive at their registered email.

Not everyone who logs in should see the same things. The admin controls the whole system,
internal staff handle day-to-day work with limited access, and customers see only their
own subscriptions and invoices through a portal. Only the admin can add a new member of
staff.

### Launching a new plan
AstraFlow is launching a new product. The admin adds it with its name, type, sales price,
cost price and notes, and offers it in variants, for example by brand, where choosing the
Odoo brand adds 560 to the price.

Then comes the plan: what it costs and how often it bills (daily, weekly, monthly or
yearly), the minimum quantity, and the dates it is available. Some plans should close on
their own when they end, and others can be closed early, paused or renewed. The admin
decides which, plan by plan.

### Turning a quote into a subscription
A sales rep is preparing an offer for a large customer. Instead of building it from
scratch, they start from a quotation template that already has the right plan and product
lines, and stays valid for a set number of days.

Each subscription gets its own number, a customer, a plan, a start date, an expiry date
and payment terms, with lines showing the product, quantity, unit price, taxes and amount.
It moves from Draft to Quotation to Confirmed to Active and finally to Closed, and anyone
on the team should be able to see where it stands.

### Billing day
It is billing day, and hundreds of subscriptions are due at once. Nobody should be typing
invoices by hand. Each one should be created from its subscription, with the customer
details, product lines, taxes and totals already filled in.

Taxes are calculated automatically from rules the admin has set. The team can confirm,
cancel, send or print any invoice, and each invoice moves from Draft to Confirmed to Paid.
Nothing should be billed twice or skipped.

### A customer asks for a better price
A customer wants a discount for taking more seats. Only the admin can create discount
rules: a fixed amount or a percentage, with a minimum purchase or quantity, start and end
dates, and a limit on how many times it can be used.

A discount can apply to specific products or to whole subscriptions. Once it applies, it
should appear on the next invoice on its own, without anyone having to remember it, and
stop when it expires or runs out.

### The money comes in
Payments arrive by different methods and on different days. Each one needs to be recorded
with its method, amount, date and a note, against the right invoice.

At any moment, the team should be able to see which invoices are paid and which are still
outstanding, so overdue ones can be chased before they turn into lost revenue.

### The finance lead, at the end of the month
How many subscriptions are active? How much revenue came in, and how much is still owed?
Which invoices are overdue? Today, answering those questions takes days.

The finance lead wants one place to see active subscriptions, revenue, payments and overdue
invoices, with filters and a summary view. With thousands of subscriptions on the books,
every page should still respond in under two seconds.
