// Prints ESLint findings one per line (file:line rule message). Used because the default formatter is verbose.
import { ESLint } from "eslint";

const eslint = new ESLint();
const results = await eslint.lintFiles(["src"]);
let n = 0;
for (const f of results) {
  for (const m of f.messages) {
    n += 1;
    console.log(`${f.filePath.split("src")[1]}:${m.line} ${m.ruleId} ${m.message.split("\n")[0].slice(0, 100)}`);
  }
}
console.log(n === 0 ? "eslint: clean" : `eslint: ${n} finding(s)`);
process.exit(n === 0 ? 0 : 1);
