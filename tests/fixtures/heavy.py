"""CPU-bound manual fixture for real hardware validation."""

value = 0
for number in range(200_000_000):
    value += number % 7
print(value)
