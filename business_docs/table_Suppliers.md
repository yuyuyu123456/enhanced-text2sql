# Business Document: Suppliers Table

## 1. Business Purpose
The Suppliers table represents a database model that tracks the vendors or suppliers from which a department store procures goods. It serves as a repository for supplier details, which can be used to manage and analyze the relationships with external parties that supply products to the store.

## 2. Column Descriptions
- **supplier_id (INTEGER)**: A unique identifier for each supplier. This serves as the primary key and ensures that each supplier can be uniquely identified and referenced throughout the database.
- **supplier_name (VARCHAR(80))**: The name of the supplier. This is the business entity that provides goods to the department store.
- **supplier_phone (VARCHAR(80))**: The contact phone number of the supplier. This can be used to reach out to the supplier for order inquiries, delivery issues, or other communication purposes.

## 3. Aggregation Methods
- **supplier_id**: Aggregating this column is less common, but you might want to COUNT the number of suppliers for analysis purposes.
- **supplier_name**: No direct aggregation metrics can be calculated from this column, as it's a string identifier.
- **supplier_phone**: Similar to supplier_name, no direct aggregation is possible here.

## 4. Calculable Metrics
- **Number of Suppliers**: The total count of unique suppliers (using COUNT(supplier_id)).
- **Supplier Contact Ratio**: If there's a table tracking orders or products, this could be used to calculate the proportion of products that come from a specific supplier (using COUNT(DISTINCT order_id) or COUNT(DISTINCT product_id) divided by total number of orders or products).

## 5. Common Filters
- **Filter by Name**: Retrieve information about a specific supplier using `WHERE supplier_name = 'Supplier Name'`.
- **Filter by Phone Number**: If looking to contact a supplier about a particular matter, you might filter using `WHERE supplier_phone = 'Phone Number'`.
- **Active Suppliers**: Filtering suppliers based on the date when their contact information was last updated or their last transaction date to determine active suppliers.

## 6. Join Guidance
- **Products Table**: To see which products are supplied by a specific supplier, you can join the Suppliers table with the Products table using the supplier_id as a common key.
- **Orders Table**: To understand which suppliers have provided products for orders, you can join with the Orders table using a foreign key in the Orders table that references the supplier_id.
- **Purchases Table**: To calculate the total amount spent with a particular supplier or to identify the supplier with the highest purchases, a join with the Purchases table is required.

## 7. Query Patterns

### Example Query 1
```sql
SELECT supplier_id, supplier_name, COUNT(*) as number_of_products
FROM Suppliers
JOIN Products ON Suppliers.supplier_id = Products.supplier_id
GROUP BY supplier_id, supplier_name
ORDER BY number_of_products DESC;
```
**Business Explanation**: This query lists the number of products associated with each supplier, which can help identify the supplier diversity and the variety of products offered.

### Example Query 2
```sql
SELECT supplier_name, SUM(purchase_amount) as total_spent
FROM Suppliers
JOIN Purchases ON Suppliers.supplier_id = Purchases.supplier_id
GROUP BY supplier_name
ORDER BY total_spent DESC;
```
**Business Explanation**: This query provides the total amount spent on purchases from each supplier, helping the store understand which suppliers they are spending the most with.

### Example Query 3
```sql
SELECT supplier_phone, COUNT(DISTINCT order_id) as total_orders
FROM Suppliers
JOIN Orders ON Suppliers.supplier_id = Orders.supplier_id
WHERE order_date BETWEEN '2023-01-01' AND '2023-12-31'
GROUP BY supplier_phone
ORDER BY total_orders DESC;
```
**Business Explanation**: This query finds out which suppliers had the most orders in the year 2023, which could be used to prioritize supplier relationship management or identify supplier performance trends.