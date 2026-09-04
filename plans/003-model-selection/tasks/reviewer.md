## Task 1: Find off-by-one error
Artifact:
```python
def get_last_element(arr):
    return arr[len(arr)]
```
Deliverable: `<out>/review.txt`
Find the defect in the python function. Scorer checks that the review mentions `IndexError` or `len(arr) - 1`.

## Task 2: Unhandled edge case
Artifact:
```python
def divide(a, b):
    return a / b
```
Deliverable: `<out>/review.txt`
Identify the missing error handling. Scorer checks that `ZeroDivisionError` or checking if `b == 0` is mentioned.

## Task 3: Insecure command
Artifact:
```python
import os
def run_user_cmd(user_input):
    os.system(f"echo {user_input}")
```
Deliverable: `<out>/review.txt`
Identify the security vulnerability. Scorer checks that command injection or shell injection is mentioned.

## Task 4: Resource leak
Artifact:
```python
def read_file(path):
    f = open(path, 'r')
    return f.read()
```
Deliverable: `<out>/review.txt`
Identify the resource management issue. Scorer checks that missing `f.close()` or missing `with` statement is mentioned.

## Task 5: Mutable default argument
Artifact:
```python
def append_item(item, lst=[]):
    lst.append(item)
    return lst
```
Deliverable: `<out>/review.txt`
Identify the issue with the function signature. Scorer checks that "mutable default argument" or using `lst=None` is mentioned.
