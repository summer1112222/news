#!/usr/bin/env node
// 小程序上传脚本:把 miniprogram/ 打包上传为开发版本(体验版)。
// 密钥经环境变量 MP_PRIVATE_KEY 传入(密钥文件全文),不落任何日志 —— 与 push_serverchan 同约定。
// 用法:MP_APPID=wx**** MP_PRIVATE_KEY="$(cat private.key)" node scripts/mp_upload.mjs
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

const require_ = createRequire(import.meta.url);
const ci = require_('miniprogram-ci');

const appid = (process.env.MP_APPID || '').trim();
const privateKey = (process.env.MP_PRIVATE_KEY || '').trim();

if (!appid || !privateKey) {
  console.error('❌ 未设置 MP_APPID / MP_PRIVATE_KEY 环境变量(AppID 与小程序代码上传密钥)');
  process.exit(1);
}

// 私钥内容写入临时文件(ci 需要 privateKeyPath),上传完即删
const keyPath = path.join(os.tmpdir(), `mp-key-${Date.now()}-${Math.random().toString(36).slice(2)}`);
fs.writeFileSync(keyPath, privateKey + (privateKey.endsWith('\n') ? '' : '\n'), { mode: 0o600 });

function clean(msg) {
  // 防御性脱敏:任何输出里若意外带出私钥内容,替换为 ***
  return String(msg).replace(new RegExp(privateKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), '***');
}

async function main() {
  const today = require_('../miniprogram/data/today.js');
  if (!today.date) {
    console.error('❌ miniprogram/data/today.js 缺 date(先跑 python scripts/build_mp_data.py)');
    process.exit(1);
  }
  const version = today.date;
  const desc = `${today.date} ${new Date().toTimeString().slice(0, 5)}`;

  const project = new ci.Project({
    appid,
    type: 'miniProgram',
    projectPath: path.resolve('miniprogram'),
    privateKeyPath: keyPath,
    ignores: ['node_modules/**/*'],
  });

  try {
    const result = await ci.upload({
      project,
      version,
      desc,
      setting: { es6: true, minify: true },
      robot: 1,
    });
    console.log(`[上传] 版本 ${version} 完成`, result && result.subPackageInfo ? result.subPackageInfo : '');
  } catch (e) {
    console.error(`❌ 上传失败: ${clean(e && e.message ? e.message : e)}`);
    process.exitCode = 1;
  } finally {
    try { fs.unlinkSync(keyPath); } catch (_) { /* 临时文件已不存在,忽略 */ }
  }
}

main().catch((e) => {
  try { fs.unlinkSync(keyPath); } catch (_) { /* 忽略 */ }
  console.error(`❌ 上传异常: ${clean(e && e.message ? e.message : e)}`);
  process.exit(1);
});
