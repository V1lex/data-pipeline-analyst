import { test, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

test.skip(!process.env.FRONTEND_E2E_URL, 'Start the isolated QA API and set FRONTEND_E2E_URL.');

async function prepare(page) {
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'С чего начнём?'})).toBeVisible();
  const message=page.getByRole('textbox',{name:'Сообщение аналитику'});
  await message.fill('Выручка по городам\nЗаказы из s3://lake/orders/, только COMPLETED, результат в s3://lake/mart/orders/.');
  await message.press('Enter');
  await expect(page.getByText('Требования собраны. Проверьте спецификацию.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Собрать спецификацию',exact:true}).click();
  await expect(page.locator('.pa-context-drawer')).toBeVisible();
  await expect(page.getByRole('button',{name:'Согласовать',exact:true})).toBeVisible();
}
async function screenshot(page,name) {
  if(!process.env.DPA_QA_SCREENSHOTS)return;
  await mkdir(process.env.DPA_QA_SCREENSHOTS,{recursive:true});
  await page.screenshot({path:path.join(process.env.DPA_QA_SCREENSHOTS,name),fullPage:true});
}

test('chat to approved model, real TLC, saved code, reload and version-bound download',async({page})=>{
  await prepare(page);
  await page.getByRole('tab',{name:'Спецификация',exact:true}).click();
  await expect(page.locator('.pa-specification-json')).toContainText('raw_orders');
  await page.getByRole('button',{name:'Согласовать',exact:true}).click();
  await expect(page.getByRole('button',{name:'Проверить модель',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Проверить модель',exact:true}).click();
  await expect(page.locator('.pa-verification-summary')).toHaveText('Модель проверена',{timeout:30000});
  await page.getByRole('button',{name:'Получить код',exact:true}).click();
  await expect(page.locator('.pa-file-name')).toContainText('pipeline-');
  await screenshot(page,'chat-desktop-files.png');
  const downloadEvent=page.waitForEvent('download');
  await page.locator('.pa-file-download').click();
  const download=await downloadEvent;
  expect(download.suggestedFilename()).toMatch(/^pipeline-.*\.py$/);
  expect(await download.failure()).toBeNull();
  await page.getByRole('button',{name:'Закрыть панель',exact:true}).click();
  await screenshot(page,'chat-desktop.png');
  await page.reload();
  await expect(page.getByText('Требования собраны. Проверьте спецификацию.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Файлы',exact:true}).click();
  await expect(page.locator('.pa-file-name')).toContainText('pipeline-');
  await page.locator('.pa-file-name').click();
  await expect(page.locator('.pa-file-preview pre')).toHaveText('print(1)\n');
});

test('mobile drawer errors stay readable and close restores composer/focus',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await prepare(page);
  const dialog=page.getByRole('dialog',{name:'Схема',exact:true});
  await expect(dialog).toBeVisible();
  await page.route('**/api/v1/specifications/*/approve',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'STORAGE_ERROR',message:'Не удалось согласовать. Повторите запрос.'}})}));
  await dialog.getByRole('button',{name:'Согласовать',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('Не удалось согласовать');
  await screenshot(page,'chat-mobile-drawer.png');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('textbox',{name:'Сообщение аналитику'})).toBeFocused();
  await page.getByRole('button',{name:'Открыть историю диалогов'}).click();
  await expect(page.getByRole('dialog',{name:'Pipeline',exact:true})).toBeVisible();
  await expect(page.getByText('user',{exact:true})).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button',{name:'Открыть историю диалогов'})).toBeFocused();
  await screenshot(page,'chat-mobile.png');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('provider error preserves first-turn draft and retry does not create another conversation',async({page})=>{
  await page.goto('/');
  const text=page.getByRole('textbox',{name:'Сообщение аналитику'});
  await text.fill('Ошибка провайдера');
  await text.press('Enter');
  await expect(page.getByRole('alert')).toContainText('Модель не смогла обработать запрос');
  await expect(text).toHaveValue('Ошибка провайдера');
  const selected=await page.locator('.pa-navigation__project[aria-current="page"]').textContent();
  await text.fill('Исправленные требования');
  await text.press('Enter');
  await expect(page.getByText('Требования собраны. Проверьте спецификацию.',{exact:true})).toBeVisible();
  await expect(page.locator('.pa-navigation__project[aria-current="page"]')).toHaveText(selected);
  await expect(page.locator('.pa-conversation__message--user')).toHaveCount(1);
});

test('sidebar rename persists, delete needs confirmation, and typing has one rounded focus border',async({page})=>{
  await prepare(page);
  await page.keyboard.press('Escape');
  const selected=page.locator('.pa-navigation__project[aria-current="page"]');
  const oldName=(await selected.textContent()).trim();
  await selected.locator('..').getByRole('button',{name:`Действия диалога ${oldName}`,exact:true}).click();
  await page.getByRole('button',{name:'Переименовать',exact:true}).click();
  await page.getByRole('textbox',{name:'Название диалога',exact:true}).fill('Новый заголовок');
  await page.getByRole('button',{name:'Сохранить',exact:true}).click();
  await expect(selected).toHaveText('Новый заголовок');
  await page.reload();
  await expect(page.locator('.pa-conversation-title')).toHaveText('Новый заголовок');
  const text=page.getByRole('textbox',{name:'Сообщение аналитику'});
  await text.fill('Ещё сообщение');
  await expect(text).toBeFocused();
  expect(await text.evaluate(el=>getComputedStyle(el).outlineStyle)).toBe('none');
  expect(await page.locator('.pa-conversation__composer').evaluate(el=>getComputedStyle(el).outlineStyle)).toBe('none');
  await screenshot(page,'chat-management-composer.png');
  await page.getByRole('button',{name:'Действия диалога Новый заголовок',exact:true}).click();
  await page.getByRole('button',{name:'Удалить',exact:true}).click();
  await screenshot(page,'chat-management-confirm.png');
  await page.getByRole('button',{name:'Отмена',exact:true}).click();
  await expect(selected).toHaveText('Новый заголовок');
  await page.getByRole('button',{name:'Действия диалога Новый заголовок',exact:true}).click();
  await page.getByRole('button',{name:'Удалить',exact:true}).click();
  await page.getByRole('button',{name:'Удалить диалог',exact:true}).click();
  await expect(page.getByRole('heading',{name:'С чего начнём?'})).toBeVisible();
  await expect(page.locator('.pa-navigation__project').filter({hasText:'Новый заголовок'})).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading',{name:'С чего начнём?'})).toBeVisible();
  await expect(page.locator('.pa-navigation__project').filter({hasText:'Новый заголовок'})).toHaveCount(0);
});

test('typing focus uses the rounded composer without nested rectangles',async({page})=>{
  await page.goto('/');
  const text=page.getByRole('textbox',{name:'Сообщение аналитику'});
  await text.fill('Текст в поле');
  expect(await text.evaluate(el=>getComputedStyle(el).outlineStyle)).toBe('none');
  expect(await page.locator('.pa-conversation__composer').evaluate(el=>getComputedStyle(el).outlineStyle)).toBe('none');
});

test('mobile rename failure keeps the form accessible and Escape cancels without closing history',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await prepare(page);
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'Открыть историю диалогов'}).click();
  const history=page.getByRole('dialog',{name:'Pipeline',exact:true});
  const name=(await history.locator('.pa-navigation__project[aria-current="page"]').textContent()).trim();
  await history.locator('.pa-navigation__project[aria-current="page"]').locator('..').getByRole('button',{name:`Действия диалога ${name}`,exact:true}).click();
  await history.getByRole('button',{name:'Переименовать',exact:true}).click();
  const title=history.getByRole('textbox',{name:'Название диалога',exact:true});
  await title.fill('Переименовать на телефоне');
  await page.route('**/api/v1/projects/*',route=>route.request().method()==='PATCH' ? route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'STORAGE_ERROR',message:'Сохранение недоступно'}})}) : route.continue());
  await history.getByRole('button',{name:'Сохранить',exact:true}).click();
  await expect(history.getByRole('alert')).toBeVisible();
  await expect(title).toHaveValue('Переименовать на телефоне');
  await page.keyboard.press('Escape');
  await expect(title).not.toBeVisible();
  await expect(history).toBeVisible();
  await expect(history.locator('.pa-navigation__project[aria-current="page"]').locator('..').getByRole('button',{name:`Действия диалога ${name}`,exact:true})).toBeFocused();
  await page.unroute('**/api/v1/projects/*');
  await history.locator('.pa-navigation__project[aria-current="page"]').locator('..').getByRole('button',{name:`Действия диалога ${name}`,exact:true}).click();
  await history.getByRole('button',{name:'Переименовать',exact:true}).click();
  await history.getByRole('textbox',{name:'Название диалога',exact:true}).fill('Мобильный диалог');
  await history.getByRole('button',{name:'Сохранить',exact:true}).click();
  await expect(history.locator('.pa-navigation__project[aria-current="page"]')).toHaveText('Мобильный диалог');
  await screenshot(page,'chat-management-mobile.png');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
