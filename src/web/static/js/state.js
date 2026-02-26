/* Shared mutable state + API config */

export const API = '';  // same origin

export const state = {
    session: null,    // current session ID from URL ?session=
    catalog: null,    // cached catalog API response
    activeStep: 'catalog',
};
