import { reverseString } from './utils/stringUtils';
import { addNumbers } from './utils/mathUtils';
import { UserType } from './types';

// 测试用户对象
const user: UserType = {
    name: "Alice",
    age: 25
};

// 使用工具函数
function processUser(user: UserType): void {
    const reversed = reverseString(user.name);
    const newAge = addNumbers(user.age, 1);
    console.log(`Reversed name: ${reversed}, New age: ${newAge}`);
}

processUser(us