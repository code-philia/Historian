export function greetUser(name) {
    console.log(`Hello, ${name}! Welcome to our application.`);
}

/**
 * Calculates the sum of two numbers after incrementing the first parameter by 1.
 *
 * @param {number} a - The first number (will be incremented by 1 before addition)
 * @param {number} b - The second number
 * @returns {number} The sum of (a + 1) and b
 */
export function calculateSum(a, b) {
    a += 1;
    return a + b;
}