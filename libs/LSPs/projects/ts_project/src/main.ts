import { reverseString } from './utils/stringUtils';
import { addNumbers } from './utils/mathUtils';
import { UserType } from './types';

// Test user object
const user: UserType = {
    name: "Alice",
    age: 25
};

// Use utility functions
function processUser(user: UserType): void {
    const reversed = reverseString(user.name);
    const newAge = addNumbers(user.age, 1);
    console.log(`Reversed name: ${reversed}, New age: ${newAge}`);
}

processUser(us