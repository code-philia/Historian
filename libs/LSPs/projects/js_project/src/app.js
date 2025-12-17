import { greetUser, calculateSum } from './utils.js';
import { APP_NAME } from './config.js';

const user = 'Alice';

console.log(`${APP_NAME}: Initializing application...`);

function startApp(username) {
    greetUser(username);
    const sum = calculateSum(5, 10);
}

startApp(user);