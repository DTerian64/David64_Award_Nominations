import React, { useState } from 'react';
import { UserCog, X, Search } from 'lucide-react';
import { useImpersonation } from '../contexts/ImpersonationContext';

interface User {
  UserId: number;
  userPrincipalName: string;
  FirstName: string;
  LastName: string;
  Title: string;
}

interface AdminImpersonationPanelProps {
  users: User[];
}

export const AdminImpersonationPanel: React.FC<AdminImpersonationPanelProps> = ({ users }) => {
  const { isAdmin, startImpersonation } = useImpersonation();
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  if (!isAdmin) return null;

  const filteredUsers = users.filter(user =>
    `${user.FirstName} ${user.LastName} ${user.userPrincipalName}`
      .toLowerCase()
      .includes(searchTerm.toLowerCase())
  );

  const handleImpersonate = (user: User) => {
    startImpersonation({
      UserId: user.UserId,
      userPrincipalName: user.userPrincipalName,
      FirstName: user.FirstName,
      LastName: user.LastName,
    });
    setIsOpen(false);
    setSearchTerm('');
  };

  return (
    <>
      <button
        onClick={() => setIsOpen(true)}
        className="flex items-center space-x-2 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors"
      >
        <UserCog className="w-5 h-5" />
        <span>Impersonate User</span>
      </button>

      {isOpen && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col">
            <div className="p-6 border-b border-gray-200 flex justify-between items-center">
              <h2 className="text-2xl font-bold text-gray-900">Impersonate User</h2>
              <button
                onClick={() => setIsOpen(false)}
                className="text-gray-400 hover:text-gray-600"
              >
                <X className="w-6 h-6" />
              </button>
            </div>

            <div className="p-6 border-b border-gray-200">
              <div className="relative">
                <Search className="absolute left-3 top-3 w-5 h-5 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search by name or email..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-purple-500"
                />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-6">
              {filteredUsers.length === 0 ? (
                <p className="text-center text-gray-500 py-8">No users found</p>
              ) : (
                <div className="space-y-2">
                  {filteredUsers.map(user => (
                    <button
                      key={user.UserId}
                      onClick={() => handleImpersonate(user)}
                      className="w-full text-left p-4 border border-gray-200 rounded-lg hover:bg-purple-50 hover:border-purple-300 transition-colors"
                    >
                      <div className="font-semibold text-gray-900">
                        {user.FirstName} {user.LastName}
                      </div>
                      <div className="text-sm text-gray-600">{user.Title}</div>
                      <div className="text-xs text-gray-500 mt-1">{user.userPrincipalName}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="p-6 border-t border-gray-200 bg-gray-50">
              <p className="text-sm text-gray-600">
                <strong>Note:</strong> All actions performed while impersonating will be logged with both your admin account and the impersonated user.
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
};