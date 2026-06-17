# Business Document for the Products Table

## 1. Business Purpose

The `Products` table in the `department_store` database represents the inventory of a retail store. It captures all the details about the products available for sale, including their type, name, and price.

## 2. Column Descriptions

- **product_id (INTEGER)**: A unique identifier for each product. It is used to uniquely identify a product within the database.
- **product_type_code (VARCHAR(10))**: A code that categorizes the product into a specific type or category. This helps in organizing and identifying products based on their type.
- **product_name (VARCHAR(80))**: The name of the product, which provides information about what the product is.
- **product_price (DECIMAL(19,4))**: The price at which the product is sold. It includes two decimal places for cents.

## 3. Aggregation Methods

- **product_id**: Not typically aggregated as it is a unique identifier.
- **product_type_code**: Can be used to group products by type.
  - **Metrics**: Total number of products per type (`COUNT`), average price per type (`AVG`), minimum and maximum price per type (`MIN`, `MAX`).
- **product_name**: Not typically aggregated.
- **product_price**: Can be aggregated to provide insights into pricing.
  - **Metrics**: Total revenue (`SUM`), average price (`AVG`), minimum price (`MIN`), maximum price (`MAX`).

## 4. Calculable Metrics

- **Average Product Price**: The average price of all products in the database.
  - Formula: `AVG(product_price)`
- **Total Revenue**: The total revenue generated from all products sold.
  - Formula: `SUM(product_price)`
- **Lowest and Highest Product Prices**: The lowest and highest prices at which products are sold.
  - Metrics: `MIN(product_price)`, `MAX(product_price)`

## 5. Common Filters

- **Product Type**: Filtering products by a specific type.
  - Example: `WHERE product_type_code = 'XYZ'`
- **Price Range**: Filtering products within a specific price range.
  - Example: `WHERE product_price BETWEEN 10.00 AND 50.00`
- **Product Name**: Filtering products by a specific name.
  - Example: `WHERE product_name LIKE '%Widget%'`

## 6. Join Guidance

- **Orders Table**: Joining with the `Orders` table is useful to calculate total sales, average order value, and inventory levels.
  - Example: `JOIN Orders ON Products.product_id = Orders.product_id`
- **Product Types Table**: Joining with a `ProductTypes` table (hypothetical) can provide additional information about product categories.
  - Example: `JOIN ProductTypes ON Products.product_type_code = ProductTypes.type_code`

## 7. Query Patterns

### Example Query 1: List all products in a specific category

```sql
SELECT product_id, product_name, product_price
FROM Products
WHERE product_type_code = 'XYZ';
```

**Business Explanation**: This query lists all products that belong to a specific category, which can be useful for inventory management or product marketing.

### Example Query 2: Calculate the average product price

```sql
SELECT AVG(product_price) AS average_price
FROM Products;
```

**Business Explanation**: This query calculates the average price of all products, which can be used for pricing strategy or comparing with competitors.

### Example Query 3: Find the lowest and highest product prices

```sql
SELECT MIN(product_price) AS lowest_price, MAX(product_price) AS highest_price
FROM Products;
```

**Business Explanation**: This query identifies the lowest and highest prices in the product catalog, which can be used for understanding the price range of the products and setting promotional strategies.