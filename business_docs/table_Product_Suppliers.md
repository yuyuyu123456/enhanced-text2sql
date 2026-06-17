# Business Document for Product_Suppliers Table

## 1. Business Purpose

The `Product_Suppliers` table represents the relationship between the products available in a department store and the suppliers who provide these products. It captures the supply chain aspect of the department store's inventory management, detailing the purchase history of each product from its supplier.

## 2. Column Descriptions

- **product_id (INTEGER)**: Unique identifier for the product.
- **supplier_id (INTEGER)**: Unique identifier for the supplier.
- **date_supplied_from (DATETIME)**: The start date of the supply period for the product from the supplier.
- **date_supplied_to (DATETIME)**: The end date of the supply period for the product from the supplier.
- **total_amount_purchased (VARCHAR(80))**: The total quantity of the product purchased from the supplier during the supply period.
- **total_value_purchased (DECIMAL(19,4))**: The total monetary value of the product purchased from the supplier during the supply period.

## 3. Aggregation Methods

- **SUM**: Can be used on `total_amount_purchased` and `total_value_purchased` to calculate the total quantity and total value of products purchased from a supplier or for a specific product.
- **AVG**: Can be used on `total_value_purchased` to calculate the average value of purchases per product or per supplier.
- **COUNT**: Can be used on the entire table to count the number of records, or on `product_id` to count the number of suppliers for each product.
- **MIN** and **MAX**: Can be used on `date_supplied_from` and `date_supplied_to` to find the earliest and latest supply dates, or on `total_value_purchased` to find the lowest and highest purchase values.

## 4. Calculable Metrics

- **Average Product Price**: Calculated by dividing `total_value_purchased` by `total_amount_purchased` for each product.
- **Total Orders**: Count of records in the table, representing the total number of purchases.
- **Total Spend by Supplier**: Sum of `total_value_purchased` for all purchases from a particular supplier.
- **Average Purchase Duration**: Calculated by subtracting `date_supplied_from` from `date_supplied_to` and averaging the results.

## 5. Common Filters

- **By Product**: Filtering by `product_id` to analyze purchases for a specific product.
- **By Supplier**: Filtering by `supplier_id` to analyze purchases from a specific supplier.
- **By Date Range**: Filtering by `date_supplied_from` and `date_supplied_to` to analyze purchases within a specific time frame.
- **By Purchase Value**: Filtering by `total_value_purchased` to analyze purchases above or below a certain monetary threshold.

## 6. Join Guidance

- **Products Table**: Joining with the `Products` table on `product_id` allows for retrieving product details such as product name and category.
- **Suppliers Table**: Joining with the `Suppliers` table on `supplier_id` allows for retrieving supplier details such as supplier name and contact information.

## 7. Query Patterns

### Example 1: Calculate the total value of purchases for each product

```sql
SELECT product_id, SUM(total_value_purchased) AS total_value
FROM Product_Suppliers
GROUP BY product_id;
```

This query provides the total value of purchases for each product, which can help in understanding the popularity and profitability of different products.

### Example 2: Find the average purchase duration for each supplier

```sql
SELECT supplier_id, AVG(DATEDIFF(date_supplied_to, date_supplied_from)) AS average_duration
FROM Product_Suppliers
GROUP BY supplier_id;
```

This query calculates the average duration of supply periods for each supplier, indicating the consistency and reliability of the supply chain.

### Example 3: List all products purchased from a specific supplier within a certain date range

```sql
SELECT ps.product_id, p.product_name, ps.date_supplied_from, ps.date_supplied_to
FROM Product_Suppliers ps
JOIN Products p ON ps.product_id = p.product_id
WHERE ps.supplier_id = ? AND ps.date_supplied_from BETWEEN ? AND ?;
```

This query lists all products purchased from a specific supplier within a given date range, which can be useful for inventory management and supplier performance analysis.