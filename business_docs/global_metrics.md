## Sales & Orders

| Metric Name          | Business Meaning                                                                                   | Tables Involved                          | Columns Used                                | Aggregation Formula                             | Example Question                                  |
|----------------------|------------------------------------------------------------------------------------------------------|-------------------------------------------|-----------------------------------------------|------------------------------------------------|----------------------------------------------------|
| Total Sales          | Total amount of money generated from sales.                                                         | Customers, Customer_Orders, Order_Items  | product_price, order_id                       | SUM(product_price)                              | "What is the total sales amount for last month?"  |
| Average Order Value  | Average amount of money spent on an order.                                                          | Customers, Customer_Orders, Order_Items  | product_price, order_id                       | AVG(SUM(product_price))                        | "What is the average order value for our customers?" |
| Total Orders         | Total number of orders placed.                                                                     | Customer_Orders                          | order_id                                     | COUNT(order_id)                                 | "How many orders were placed last week?"         |
| Order Completion Rate | Percentage of orders that were completed successfully.                                              | Customer_Orders                          | order_status_code, order_id                   | (COUNT(order_status_code = 'Completed') / COUNT(order_id)) * 100 | "What is our order completion rate for the last quarter?" |
| Order Cancellation Rate | Percentage of orders that were cancelled.                                                           | Customer_Orders                          | order_status_code, order_id                   | (COUNT(order_status_code = 'Cancelled') / COUNT(order_id)) * 100 | "What is our order cancellation rate for the last quarter?" |

### SQL Examples

```sql
-- Total Sales
SELECT SUM(p.product_price) AS total_sales
FROM Customers c
JOIN Customer_Orders co ON c.customer_id = co.customer_id
JOIN Order_Items oi ON co.order_id = oi.order_id
JOIN Products p ON oi.product_id = p.product_id;

-- Average Order Value
SELECT AVG(SUM(p.product_price)) AS average_order_value
FROM Customers c
JOIN Customer_Orders co ON c.customer_id = co.customer_id
JOIN Order_Items oi ON co.order_id = oi.order_id
JOIN Products p ON oi.product_id = p.product_id;

-- Total Orders
SELECT COUNT(order_id) AS total_orders
FROM Customer_Orders;

-- Order Completion Rate
SELECT (COUNT(CASE WHEN order_status_code = 'Completed' THEN 1 END) / COUNT(order_id)) * 100 AS order_completion_rate
FROM Customer_Orders;

-- Order Cancellation Rate
SELECT (COUNT(CASE WHEN order_status_code = 'Cancelled' THEN 1 END) / COUNT(order_id)) * 100 AS order_cancellation_rate
FROM Customer_Orders;
```

## Products & Inventory

| Metric Name          | Business Meaning                                                                                   | Tables Involved                           | Columns Used                                 | Aggregation Formula                             | Example Question                                  |
|----------------------|------------------------------------------------------------------------------------------------------|--------------------------------------------|-----------------------------------------------|------------------------------------------------|----------------------------------------------------|
| Total Products       | Total number of products in inventory.                                                              | Products                                  | product_id                                   | COUNT(product_id)                                | "How many products do we have in inventory?"     |
| Average Inventory Cost | Average cost of inventory per product.                                                            | Products, Product_Suppliers               | product_price, total_amount_purchased        | AVG((product_price * total_amount_purchased) / COUNT(product_id)) | "What is the average inventory cost per product?" |
| Fast Moving Products | Products that are sold quickly.                                                                     | Products, Order_Items                     | product_id, order_id                          | COUNT(product_id) WHERE product_id IN (SELECT product_id FROM Order_Items GROUP BY product_id ORDER BY COUNT(product_id) DESC LIMIT 10) | "What are our top 10 fast-moving products?"     |

### SQL Examples

```sql
-- Total Products
SELECT COUNT(product_id) AS total_products
FROM Products;

-- Average Inventory Cost
SELECT AVG((p.product_price * ps.total_amount_purchased) / COUNT(p.product_id)) AS average_inventory_cost
FROM Products p
JOIN Product_Suppliers ps ON p.product_id = ps.product_id;

-- Fast Moving Products
SELECT product_id
FROM Order_Items
GROUP BY product_id
ORDER BY COUNT(order_id) DESC
LIMIT 10;
```

## Customers

| Metric Name          | Business Meaning                                                                                   | Tables Involved                             | Columns Used                                 | Aggregation Formula                             | Example Question                                  |
|----------------------|------------------------------------------------------------------------------------------------------|---------------------------------------------|-----------------------------------------------|------------------------------------------------|----------------------------------------------------|
| Total Customers      | Total number of customers.                                                                         | Customers                                  | customer_id                                   | COUNT(customer_id)                               | "How many customers do we have?"                  |
| Average Order Count  | Average number of orders per customer.                                                             | Customers, Customer_Orders                 | customer_id, order_id                          | AVG(COUNT(order_id))                            | "What is the average order count per customer?"    |
| Customer Lifetime Value | The total amount of money a customer is expected to spend during their relationship with the company. | Customers, Customer