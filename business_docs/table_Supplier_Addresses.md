# Business Document for Supplier_Addresses Table

## 1. Business Purpose

The `Supplier_Addresses` table represents the historical and current addresses of suppliers in a department store database. It captures the relationship between suppliers and their respective addresses over time, which is crucial for tracking supplier locations and ensuring timely deliveries.

## 2. Column Descriptions

- **supplier_id (INTEGER)**: Identifies the supplier to which the address belongs.
- **address_id (INTEGER)**: Identifies the specific address of the supplier.
- **date_from (DATETIME)**: The start date when the supplier began using the address.
- **date_to (DATETIME)**: The end date when the supplier stopped using the address, or NULL if the address is still in use.

## 3. Aggregation Methods

- **date_from (DATETIME)**: Can be aggregated to find the earliest start date for a supplier or the average duration suppliers have been at an address.
- **date_to (DATETIME)**: Can be aggregated to find the latest end date, the average time suppliers have been at an address, or the count of addresses suppliers have used.
- **supplier_id (INTEGER)**: Can be aggregated to count the number of addresses each supplier has used, or the average duration at each address.
- **address_id (INTEGER)**: Can be aggregated to find the most frequently used address or the average duration at a particular address.

## 4. Calculable Metrics

- **Average Duration of Address Use**: The average time (in days) that suppliers have been at a particular address, calculated as `DATEDIFF(date_to, date_from)`.
- **Supplier Turnover Rate**: The number of addresses a supplier has used over a specific period, which can be calculated by counting the `supplier_id` entries over a given time frame.

## 5. Common Filters

- **By Supplier**: Filtering by `supplier_id` to retrieve addresses for a specific supplier.
- **By Address**: Filtering by `address_id` to retrieve all suppliers associated with a specific address.
- **By Date Range**: Filtering by `date_from` and `date_to` to find addresses active within a certain time frame.
- **Current Addresses**: Filtering for rows where `date_to` is NULL or the current date, to identify suppliers' current addresses.

## 6. Join Guidance

- **Suppliers Table**: Joining with the `Suppliers` table on `supplier_id` to get supplier details like name, contact information, etc.
- **Addresses Table**: Joining with the `Addresses` table on `address_id` to get more details about the address such as street name, city, etc.
- **Orders Table**: Joining with the `Orders` table on `supplier_id` to understand how supplier addresses are associated with orders.

## 7. Query Patterns

### Query 1: Find the average duration suppliers have been at their current address

```sql
SELECT supplier_id, AVG(DATEDIFF(CURRENT_DATE, date_from)) AS average_duration_days
FROM Supplier_Addresses
WHERE date_to IS NULL OR date_to >= CURRENT_DATE
GROUP BY supplier_id;
```

This query calculates the average duration (in days) that suppliers have been using their current addresses, which can help in understanding supplier stability.

### Query 2: List suppliers who have used more than 5 different addresses

```sql
SELECT supplier_id, COUNT(address_id) AS number_of_addresses
FROM Supplier_Addresses
GROUP BY supplier_id
HAVING COUNT(address_id) > 5;
```

This query identifies suppliers who have changed addresses frequently, which could indicate a higher turnover rate or changes in supplier needs.

### Query 3: Retrieve all suppliers' addresses that have been active in the past year

```sql
SELECT *
FROM Supplier_Addresses
WHERE date_from <= CURRENT_DATE - INTERVAL 1 YEAR
AND (date_to IS NULL OR date_to >= CURRENT_DATE - INTERVAL 1 YEAR);
```

This query helps in identifying suppliers whose addresses have been active within the last year, useful for ensuring continuous business relationships and delivery services.