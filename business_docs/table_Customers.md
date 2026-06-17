# Business Document for the Customers Table

## 1. Business Purpose
The Customers table represents the entities or individuals who purchase goods or services from the department store. It serves as a central repository for all customer-related information, enabling the store to manage customer relationships, track purchases, and personalize services.

## 2. Column Descriptions
- **customer_id (INTEGER)**: A unique identifier for each customer. It is used to reference the customer across other tables.
- **payment_method_code (VARCHAR(10))**: A code that represents the payment method used by the customer for transactions.
- **customer_code (VARCHAR(20))**: A unique code assigned to the customer by the department store for internal use.
- **customer_name (VARCHAR(80))**: The full name of the customer.
- **customer_address (VARCHAR(255))**: The physical address of the customer.
- **customer_phone (VARCHAR(80))**: The phone number of the customer.
- **customer_email (VARCHAR(80))**: The email address of the customer, used for communication and marketing purposes.

## 3. Aggregation Methods
- **customer_id**: COUNT to determine the total number of customers.
- **payment_method_code**: COUNT to analyze the popularity of different payment methods.
- **customer_code**: COUNT to track the number of customers with specific codes.
- **customer_name**: COUNT to identify the number of customers with unique names.
- **customer_address**: COUNT to understand the distribution of customers across different regions.
- **customer_phone**: COUNT to determine the number of customers with phone numbers.
- **customer_email**: COUNT to assess the number of customers with email addresses.

## 4. Calculable Metrics
- **Total Customers**: The total number of customers in the database.
- **Average Payment Method Usage**: The average number of times each payment method is used by customers.
- **Customer Code Distribution**: The distribution of customer codes across different segments or categories.

## 5. Common Filters
- **By Customer ID**: To retrieve information about a specific customer.
- **By Payment Method**: To analyze transactions made using a particular payment method.
- **By Customer Code**: To find customers with a specific internal code.
- **By Name**: To search for customers by their full name.
- **By Address**: To filter customers based on their geographic location.
- **By Phone Number**: To identify customers by their contact number.
- **By Email**: To locate customers by their email address.

## 6. Join Guidance
- **Orders Table**: To link customer purchases and track customer spending habits.
- **Transactions Table**: To analyze payment methods and transaction volumes.
- **Customer Preferences Table**: To understand customer preferences and tailor marketing strategies.

## 7. Query Patterns

### Query 1: Retrieve the total number of customers
```sql
SELECT COUNT(customer_id) AS total_customers
FROM Customers;
```
This query provides the total number of customers in the database, which is useful for assessing the size of the customer base.

### Query 2: Analyze the distribution of payment methods
```sql
SELECT payment_method_code, COUNT(payment_method_code) AS method_usage
FROM Customers
GROUP BY payment_method_code
ORDER BY method_usage DESC;
```
This query identifies the most commonly used payment methods, helping the store to understand customer preferences and optimize payment processing.

### Query 3: Find customers with a specific customer code
```sql
SELECT *
FROM Customers
WHERE customer_code = 'ABC123';
```
This query retrieves information about customers with a specific internal code, which can be useful for internal record-keeping or customer service purposes.