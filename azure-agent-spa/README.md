# Azure Operations Agent — SPA

React single-page application for the Azure Operations Agent. Provides a chat interface and dashboard view for monitoring, managing, and analyzing Azure resources.

## Features

- **Entra ID Authentication**: Sign in with Microsoft using MSAL
- **Chat Interface**: Ask questions about Azure resources, costs, health, and get AI-powered responses
- **NDJSON Streaming**: Real-time streaming of agent responses
- **Dashboard View**: Rich interactive HTML reports rendered in iframes (Chart.js visualizations)
- **Subscription Picker**: Select which Azure subscription to analyze
- **Quick Questions**: Pre-built common queries for rapid exploration
- **Dark Theme**: Consistent with the report visualizations

## Setup

1. Register an app in Azure Entra ID (SPA platform, redirect URI: `http://localhost:3000`)
2. Update `src/authConfig.js` with your `clientId` and `tenantId`
3. Grant the app `user_impersonation` delegated permission on `Azure Service Management`

## Running

```bash
npm install
npm start
```

The SPA runs on `http://localhost:3000` by default and connects to the backend API at `http://localhost:8080`.

Set `REACT_APP_API_BASE` environment variable to override the backend URL.

## Architecture

```
SPA (port 3000) → FastAPI Backend (port 8080) → MCP Server (port 3001) → Azure APIs
     ↓                    ↓
  Entra ID           Agent Framework
  MSAL auth          ChatAgent + MCP tools
```



### `npm start`

Runs the app in the development mode.\
Open [http://localhost:3000](http://localhost:3000) to view it in your browser.

The page will reload when you make changes.\
You may also see any lint errors in the console.

### `npm test`

Launches the test runner in the interactive watch mode.\
See the section about [running tests](https://facebook.github.io/create-react-app/docs/running-tests) for more information.

### `npm run build`

Builds the app for production to the `build` folder.\
It correctly bundles React in production mode and optimizes the build for the best performance.

The build is minified and the filenames include the hashes.\
Your app is ready to be deployed!

See the section about [deployment](https://facebook.github.io/create-react-app/docs/deployment) for more information.

### `npm run eject`

**Note: this is a one-way operation. Once you `eject`, you can't go back!**

If you aren't satisfied with the build tool and configuration choices, you can `eject` at any time. This command will remove the single build dependency from your project.

Instead, it will copy all the configuration files and the transitive dependencies (webpack, Babel, ESLint, etc) right into your project so you have full control over them. All of the commands except `eject` will still work, but they will point to the copied scripts so you can tweak them. At this point you're on your own.

You don't have to ever use `eject`. The curated feature set is suitable for small and middle deployments, and you shouldn't feel obligated to use this feature. However we understand that this tool wouldn't be useful if you couldn't customize it when you are ready for it.

## Learn More

You can learn more in the [Create React App documentation](https://facebook.github.io/create-react-app/docs/getting-started).

To learn React, check out the [React documentation](https://reactjs.org/).

### Code Splitting

This section has moved here: [https://facebook.github.io/create-react-app/docs/code-splitting](https://facebook.github.io/create-react-app/docs/code-splitting)

### Analyzing the Bundle Size

This section has moved here: [https://facebook.github.io/create-react-app/docs/analyzing-the-bundle-size](https://facebook.github.io/create-react-app/docs/analyzing-the-bundle-size)

### Making a Progressive Web App

This section has moved here: [https://facebook.github.io/create-react-app/docs/making-a-progressive-web-app](https://facebook.github.io/create-react-app/docs/making-a-progressive-web-app)

### Advanced Configuration

This section has moved here: [https://facebook.github.io/create-react-app/docs/advanced-configuration](https://facebook.github.io/create-react-app/docs/advanced-configuration)

### Deployment

This section has moved here: [https://facebook.github.io/create-react-app/docs/deployment](https://facebook.github.io/create-react-app/docs/deployment)

### `npm run build` fails to minify

This section has moved here: [https://facebook.github.io/create-react-app/docs/troubleshooting#npm-run-build-fails-to-minify](https://facebook.github.io/create-react-app/docs/troubleshooting#npm-run-build-fails-to-minify)
