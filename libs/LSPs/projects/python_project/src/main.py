from utils.string_utils import reverse_string
from utils.math_utils import add_numbers

class Main:
    def __init__(self):
        self.text = "Hello"
        self.reversed_text = reverse_string(self.text)
        self.result = add_numbers(5, 3)
        self.another_result = add_numbers(5, 7)

    def main(self):
        print(f"Reversed: {self.reversed_text}")
        print(f"Sum: {self.result}")

def main():
    text = "Hello"
    reversed_text = reverse_string(text)
    print(f"Reversed: {reversed_text}")
    
    result = add_numbers(5, )
    print(f"Sum: {result}")
    
    main = Main()

if __name__ == "__main__":
    main() 