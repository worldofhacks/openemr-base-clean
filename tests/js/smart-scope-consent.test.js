/**
 * @jest-environment jsdom
 */

const fs = require('fs');
const path = require('path');

const template = fs.readFileSync(
    path.resolve(__dirname, '../../templates/oauth2/scope-authorize.html.twig'),
    'utf8'
);
const scripts = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const consentScript = scripts.at(-1)[1];

function submitScopes(actionsByVersion, checkedActions = ['r', 's']) {
    document.body.innerHTML = `
        <form id="userLogin">
            <button id="authorize-btn" type="button"></button>
            <span id="spinner" class="d-none"></span>
            <input type="checkbox" class="resource-master-checkbox"
                   data-resource="Observation" data-unrestricted="1" checked>
            <input type="hidden" class="resource-context"
                   data-resource="Observation" value="user">
            <input type="hidden" class="resource-version"
                   data-resource="Observation" value="v1">
            <input type="hidden" class="resource-version-actions"
                   data-resource="Observation"
                   value='${JSON.stringify(actionsByVersion)}'>
            ${['r', 's'].map(action => `
                <input type="checkbox" class="action-checkbox"
                       data-resource="Observation" data-action="${action}"
                       ${checkedActions.includes(action) ? 'checked' : ''}>
            `).join('')}
            <div id="dynamic-scopes-container"></div>
        </form>
    `;
    const form = document.getElementById('userLogin');
    form.submit = jest.fn();
    new Function(consentScript)();
    document.dispatchEvent(new Event('DOMContentLoaded'));
    document.getElementById('authorize-btn').click();
    return [...document.querySelectorAll('#dynamic-scopes-container input')]
        .map(input => input.value)
        .sort();
}

test('mixed Observation v1 and v2 consent submits both exact requested scopes', () => {
    expect(submitScopes({v1: ['r', 's'], v2: ['r', 's']})).toEqual([
        'user/Observation.read',
        'user/Observation.rs',
    ]);
});

test('denying mixed Observation read/search submits neither representation', () => {
    expect(submitScopes({v1: ['r', 's'], v2: ['r', 's']}, [])).toEqual([]);
});

test.each([
    [{v1: ['r', 's']}, ['user/Observation.read']],
    [{v2: ['r', 's']}, ['user/Observation.rs']],
])('single-version consent never adds the other format', (formats, expected) => {
    expect(submitScopes(formats)).toEqual(expected);
});
