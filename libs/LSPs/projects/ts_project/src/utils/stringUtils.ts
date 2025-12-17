/**
 * Reverses the characters in a string.
 *
 * @param text - The string to reverse
 * @returns The reversed string
 * @throws {Error} If the input is not a string
 *
 * @example
 * ```ts
 * reverseString('hello') // returns 'olleh'
 * reverseString('TypeScript') // returns 'tpircSepyT'
 * ```
 */
export function reverseString(text: string): string {
    if (typeof text !== 'string') {
        throw new Error('Input must be a string');
    }
    return text.split('').reverse().join('');
}