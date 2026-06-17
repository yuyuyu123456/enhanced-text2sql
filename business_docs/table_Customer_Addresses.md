# Business Document for Customer_Addresses Table

## 1. Business Purpose

The `Customer_Addresses` table represents the historical and current addresses of customers within a department store. It tracks the association between customers and their respective addresses over time, including the duration for which a customer has been associated with a particular address.

## 2. Column Descriptions

- **customer_id (INTEGER)**: Identifies the customer to whom the address belongs. This is a foreign key that links to the `customer_id` in the `Customers` table.
- **address_id (INTEGER)**: Identifies the specific address associated with the customer. This is a foreign key that links to the `address_id` in the `Addresses` table.
- **date_from (DATETIME)**: The date when the customer started using the address.
- **date_to (DATETIME)**: The date when the customer stopped using the address. This column can be NULL if the address is still in use.

## 3. Aggregation Methods

- **customer_id**: Count the number of addresses each customer has.
- **address_id**: Count the number of times an address has been associated with different customers.
- **date_from**: Find the earliest date a customer started using an address.
- **date_to**: Find the latest date a customer stopped using an address.
- **duration**: Calculate the total duration a customer has been associated with an address by subtracting `date_from` from `date_to`.

## 4. Calculable Metrics

- **Average Duration of Address Usage**: Calculate the average duration a customer uses an address.
- **Total Addresses per Customer**: Count the total number of addresses per customer.
- **Active Addresses**: Count the number of addresses that are currently in use (where `date_to` is NULL).

## 5. Common Filters

- **Active Addresses**: `WHERE date_to IS NULL`
- **Addresses for a Specific Customer**: `WHERE customer_id = ?`
- **Addresses within a Date Range**: `WHERE date_from BETWEEN ? AND ?`
- **Addresses that Changed**: `WHERE date_to IS NOT NULL AND date_from < ?`

## 6. Join Guidance

- **Join with `Customers` table**: To get additional information about the customer, such as their name or contact details.
- **Join with `Addresses` table**: To get more details about the address, such as the street name, city, or ZIP code.

These joins are useful for customer profiling, analyzing customer movement patterns, and understanding the distribution of customers across different locations.

## 7. Query Patterns

### Example 1: Find the average duration of address usage for all customers

```sql
SELECT AVG(DATEDIFF(date_to, date_from)) AS average_duration
FROM Customer_Addresses
WHERE date_to IS NOT NULL;
```

This query calculates the average duration for which customers have been using their addresses.

### Example 2: Count the total number of addresses per customer

```sql
SELECT customer_id, COUNT(address_id) AS total_addresses
FROM Customer_Addresses
GROUP BY customer_id;
```

This query provides the total number of addresses associated with each customer, which can be used to identify frequent movers or loyal customers.

### Example 3: Find the number of active addresses for all customers

```sql
SELECT customer_id, COUNT(address_id) AS active_addresses
FROM Customer_Addresses
WHERE date_to IS NULL
GROUP BY customer_id;
```

This query identifies the number of addresses that are currently active for each customer, helping to understand customer mobility and current customer base.